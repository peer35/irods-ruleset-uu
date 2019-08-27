# \file      uuDatarequest.py
# \brief     Functions to handle data requests.
# \copyright Copyright (c) 2019 Utrecht University. All rights reserved.
# \license   GPLv3, see LICENSE.

import irods_types
from datetime import datetime
from genquery import (row_iterator, AS_DICT)
from smtplib import SMTP
from email.mime.text import MIMEText


def uuMetaAdd(callback, objType, objName, attribute, value):
    keyValPair = callback.msiString2KeyValPair(attribute + "=" + value,
                                               irods_types.KeyValPair())['arguments'][1]
    retval = callback.msiSetKeyValuePairsToObj(keyValPair, objName, objType)


# \brief Send an email using the specified parameters
#
# \param[in] to       Recipient email address
# \param[in] subject  Email message subject
# \param[in] body     Email message body
#
def sendMail(to, subject, body):
        # Construct message
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = "test@example.org"
        msg["To"] = to

        # Send message
        #
        # TO-DO: fetch credentials (smtp_server_address, email_address,
        # password) from credential store
        s = SMTP('smtp_server_address')
        s.starttls()
        s.login("email_address", "password")
        s.sendmail("from_email_address", to, msg.as_string()) # When testing,
                                                              # replace to with
                                                              # hardcoded email
                                                              # address
        s.quit()


# \brief Return groups and related data.
#        Copied from irods-ruleset-uu/uuGroup.py.
#
def getGroupData(callback):
    groups = {}

    # First query: obtain a list of groups with group attributes.
    ret_val = callback.msiMakeGenQuery(
        "USER_GROUP_NAME, META_USER_ATTR_NAME, META_USER_ATTR_VALUE",
        "USER_TYPE = 'rodsgroup'",
        irods_types.GenQueryInp())
    query = ret_val["arguments"][2]

    ret_val = callback.msiExecGenQuery(query, irods_types.GenQueryOut())
    while True:
        result = ret_val["arguments"][1]
        for row in range(result.rowCnt):
            name = result.sqlResult[0].row(row)
            attr = result.sqlResult[1].row(row)
            value = result.sqlResult[2].row(row)

            # Create/update group with this information.
            try:
                group = groups[name]
            except Exception:
                group = {
                    "name": name,
                    "managers": [],
                    "members": [],
                    "read": []
                }
                groups[name] = group
            if attr in ["data_classification", "category", "subcategory"]:
                group[attr] = value
            elif attr == "description":
                # Deal with legacy use of '.' for empty description metadata.
                # See uuGroupGetDescription() in uuGroup.r for correct behavior of the old query interface.
                group[attr] = '' if value == '.' else value
            elif attr == "manager":
                group["managers"].append(value)

        # Continue with this query.
        if result.continueInx == 0:
            break
        ret_val = callback.msiGetMoreRows(query, result, 0)
    callback.msiCloseGenQuery(query, result)

    # Second query: obtain list of groups with memberships.
    ret_val = callback.msiMakeGenQuery(
        "USER_GROUP_NAME, USER_NAME, USER_ZONE",
        "USER_TYPE != 'rodsgroup'",
        irods_types.GenQueryInp())
    query = ret_val["arguments"][2]

    ret_val = callback.msiExecGenQuery(query, irods_types.GenQueryOut())
    while True:
        result = ret_val["arguments"][1]
        for row in range(result.rowCnt):
            name = result.sqlResult[0].row(row)
            user = result.sqlResult[1].row(row)
            zone = result.sqlResult[2].row(row)

            if name != user and name != "rodsadmin" and name != "public":
                user = user + "#" + zone
                if name.startswith("read-"):
                    # Match read-* group with research-* or initial-* group.
                    name = name[5:]
                    try:
                        # Attempt to add to read list of research group.
                        group = groups["research-" + name]
                        group["read"].append(user)
                    except Exception:
                        try:
                            # Attempt to add to read list of initial group.
                            group = groups["initial-" + name]
                            group["read"].append(user)
                        except Exception:
                            pass
                elif not name.startswith("vault-"):
                    # Ardinary group.
                    group = groups[name]
                    group["members"].append(user)

        # Continue with this query.
        if result.continueInx == 0:
            break
        ret_val = callback.msiGetMoreRows(query, result, 0)
    callback.msiCloseGenQuery(query, result)

    return groups.values()


# \brief Check if a user is a member of the given group.
#
# \param[in] group  Name of group
# \param[in] user   Name of user
#
def groupUserMember(group, user, callback):
    groups = getGroupData(callback)
    groups = list(filter(lambda grp: group == grp["name"] and
                                     user in grp["members"], groups))

    return "true" if len(groups) == 1 else "false"


# \brief Persist a data request to disk.
#
# \param[in] data       JSON-formatted contents of the data request.
# \param[in] proposalId Unique identifier of the research proposal.
#
def submitDatarequest(callback, data, rei):
    status = -1
    statusInfo = "Internal server error"

    try:
        # Create collection
        zonePath = '/tempZone/home/datarequests-research/'
        timestamp = datetime.now().strftime('%s')
        collPath = zonePath + str(timestamp)
        callback.msiCollCreate(collPath, 1, 0)

        # Write data request data to disk
        filePath = collPath + '/' + 'datarequest.json'
        ret_val = callback.msiDataObjCreate(filePath, "", 0)
        fileDescriptor = ret_val['arguments'][2]
        callback.msiDataObjWrite(fileDescriptor, data, 0)
        callback.msiDataObjClose(fileDescriptor, 0)

        # Set the proposal fields as AVUs on the proposal JSON file
        rule_args = [filePath, "-d", "root", data]
        setJsonToObj(rule_args, callback, rei)

        # Set the status metadata field to "submitted"
        uuMetaAdd(callback, "-d", filePath, "status", "submitted")

        # Set permissions for certain groups on the subcollection
        callback.msiSetACL("recursive", "write",
                           "datarequests-research-datamanagers", collPath)
        callback.msiSetACL("recursive", "write",
                           "datarequests-research-board-of-directors", collPath)

        status = 0
        statusInfo = "OK"
    except:
        pass

    return {'status': status, 'statusInfo': statusInfo}


# \brief Retrieve a data request.
#
# \param[in] requestId Unique identifier of the data request.
#
def getDatarequest(callback, requestId):
    status = -1
    statusInfo = "Internal server error"

    try:
        # Construct filename
        collName = '/tempZone/home/datarequests-research/' + requestId
        fileName = 'datarequest.json'
        filePath = collName + '/' + fileName

        # Get the size of the datarequest JSON file and the request's status
        results = []
        rows = row_iterator(["DATA_SIZE", "COLL_NAME", "META_DATA_ATTR_VALUE"],
                            ("COLL_NAME = '%s' AND " +
                             "DATA_NAME = '%s' AND " +
                             "META_DATA_ATTR_NAME = 'status'") % (collName,
                                                                  fileName),
                            AS_DICT,
                            callback)
        for row in rows:
            collName = row['COLL_NAME']
            dataSize = row['DATA_SIZE']
            requestStatus = row['META_DATA_ATTR_VALUE']

        # Get the contents of the datarequest JSON file
        ret_val = callback.msiDataObjOpen("objPath=%s" % filePath, 0)
        fileDescriptor = ret_val['arguments'][1]
        ret_val = callback.msiDataObjRead(fileDescriptor, dataSize,
                                          irods_types.BytesBuf())
        fileBuffer = ret_val['arguments'][2]
        callback.msiDataObjClose(fileDescriptor, 0)
        requestJSON = ''.join(fileBuffer.buf)

        status = 0
        statusInfo = "OK"
    except:
        requestJSON = ""
        requestStatus = ""

    return {'requestJSON': requestJSON,
            'requestStatus': requestStatus, 'status': status,
            'statusInfo': statusInfo}




# \brief Check if the invoking user is also the owner of a given data request.
#
# \param[in] requestId        Unique identifier of the data request.
# \param[in] currentUserName  Username of the user whose ownership is checked.
#
# \return A boolean specifying whether the user owns the data request.
#
def isRequestOwner(callback, requestId, currentUserName):
    status = -1
    statusInfo = "Internal server error"
    isRequestOwner = True

    # Get username of data request owner
    try:
        # Construct path to the collection of the datarequest
        zoneName = ""
        clientZone = callback.uuClientZone(zoneName)['arguments'][0]
        collPath = ("/" + clientZone + "/home/datarequests-research/" +
                    requestId)

        # Query iCAT for the username of the owner of the data request
        rows = row_iterator(["DATA_OWNER_NAME"],
                            ("DATA_NAME = 'datarequest.json' and COLL_NAME like "
                            + "'%s'" % collPath),
                            AS_DICT, callback)

        # Extract username from query results
        requestOwnerUserName = []
        for row in rows:
            requestOwnerUserName.append(row["DATA_OWNER_NAME"])

        # Check if exactly 1 owner was found. If not, wipe
        # requestOwnerUserName list and set error status code
        if len(requestOwnerUserName) != 1:
            status = -2
            statusInfo = ("Not exactly 1 owner found. " +
                          "Something is probably wrong.")
            raise Exception()

        # We only have 1 owner. Set requestOwnerUserName to this owner
        requestOwnerUserName = requestOwnerUserName[0]

        # Compare the request owner username to the username of the current
        # user to determine ownership
        isRequestOwner = requestOwnerUserName == currentUserName

        # Set status to OK
        status = 0
        statusInfo = "OK"
    except:
        pass

    # Return data
    return {'isRequestOwner': isRequestOwner, 'status': status,
            'statusInfo': statusInfo}


# \brief  Check if the invoking user is assigned as reviewer to the given data
#         request
#
# \param[in] requestId        Unique identifier of the data request.
# \param[in] currentUserName  Username of the user that is to be checked.
#
# \return A boolean specifying whether the user is assigned as reviewer to the
#         data request.
#
def isReviewer(callback, requestId, currentUsername):
    status = -1
    statusInfo = "Internal server error"
    isReviewer = False

    try:
        # Reviewers are stored in one or more assignedForReview attributes on
        # the data request, so our first step is to query the metadata of our
        # data request file for these attributes

        # Declare variables needed for retrieving the list of reviewers
        collName = '/tempZone/home/datarequests-research/' + requestId
        fileName = 'datarequest.json'
        reviewers = []

        # Retrieve list of reviewers
        rows = row_iterator(["META_DATA_ATTR_VALUE"],
                            ("COLL_NAME = '%s' AND " +
                             "DATA_NAME = '%s' AND " +
                             "META_DATA_ATTR_NAME = 'assignedForReview'") % (collName,
                                                                  fileName),
                            AS_DICT,
                            callback)
        for row in rows:
            reviewers.append(row['META_DATA_ATTR_VALUE'])

        # Check if the reviewers list contains the current user
        isReviewer = currentUsername in reviewers

        # Set status to OK
        status = 0
        statusInfo = "OK"
    except:
        pass

    # Return the isReviewer boolean
    return {"isReviewer": isReviewer, "status": status,
            "statusInfo": statusInfo}


# \brief Assign a data request to one or more DMC members for review
#
# \param[in] assignees          JSON-formatted array of DMC members
# \param[in] requestId          Unique identifier of the data request
#
def assignRequest(callback, assignees, requestId):
    status = -1
    statusInfo = "Internal server error"

    try:
        # Construct data request collection path
        requestColl = ('/tempZone/home/datarequests-research/' +
                        requestId)

        # Check if data request has already been assigned. If true, set status
        # code to failure and do not perform requested assignment
        results = []
        rows = row_iterator(["META_DATA_ATTR_VALUE"],
                        ("COLL_NAME = '%s' and DATA_NAME = '%s' and " +
                         "META_DATA_ATTR_NAME = 'status'")
                        % (requestColl, 'datarequest.json'),
                        AS_DICT, callback)
        for row in rows:
            requestStatus = row['META_DATA_ATTR_VALUE']
        if not requestStatus == "submitted":
            status = -1
            statusInfo = "Proposal is already assigned."
            raise Exception()

        # Assign the data request by adding a delayed rule that sets one or more
        # "assignedForReview" attributes on the datarequest (the number of
        # attributes is determined by the number of assignees) ...
        status = ""
        statusInfo = ""
        callback.requestDatarequestMetadataChange(requestColl,
                                                  "assignedForReview",
                                                  assignees,
                                                  str(len(
                                                      json.loads(assignees))),
                                                  status, statusInfo)

        # ... and triggering the processing of delayed rules
        callback.adminDatarequestActions()

        # Add and execute a delayed rule for setting the status to "assigned"
        status = ""
        statusInfo = ""
        callback.requestDatarequestMetadataChange(requestColl, "status",
                                                  "assigned", "", status,
                                                  statusInfo)
        callback.adminDatarequestActions()

# \brief Persist a data request review to disk.
#
# \param[in] data       JSON-formatted contents of the data request review
# \param[in] proposalId Unique identifier of the research proposal
#
def submitReview(callback, data, requestId, rei):
    status = -1
    statusInfo = "Internal server error"

    try:
        # Check if user is a member of the Data Management Committee. If not, do
        # not allow submission of the review
        isDmcMember = False
        name = ""
        isDmcMember = groupUserMember("datarequests-research-data-management-committee",
                                      callback.uuClientFullNameWrapper(name)
                                          ['arguments'][0], callback)
        if not isDmcMember:
            status = -2
            statusInfo = "User is not a member of the Data Management Committee."
            raise Exception()

        # Check if the user has been assigned as a reviewer. If not, do not
        # allow submission of the review
        name = ""
        username = callback.uuClientNameWrapper(name)['arguments'][0]

        if not isReviewer(callback, requestId, username)['isReviewer']:
            status = -3
            statusInfo = "User is not assigned as a reviewer to this request."
            raise Exception()

        # Construct path to collection of review
        zonePath = '/tempZone/home/datarequests-research/'
        collPath = zonePath + requestId

        # Get username
        name = ""
        clientName = callback.uuClientNameWrapper(name)['arguments'][0]

        # Write review data to disk
        reviewPath = collPath + '/review_' + clientName + '.json'
        ret_val = callback.msiDataObjCreate(reviewPath, "", 0)
        fileDescriptor = ret_val['arguments'][2]
        callback.msiDataObjWrite(fileDescriptor, data, 0)
        callback.msiDataObjClose(fileDescriptor, 0)

        # Give read permission on the review to Board of Director members
        callback.msiSetACL("default", "read",
                           "datarequests-research-board-of-directors",
                           reviewPath)

        # Remove the assignedForReview attribute of this user by first fetching
        # the list of reviewers ...
        collName = '/tempZone/home/datarequests-research/' + requestId
        fileName = 'datarequest.json'
        reviewers = []
        zoneName = ""
        clientZone = callback.uuClientZone(zoneName)['arguments'][0]

        ret_val = callback.msiMakeGenQuery(
            "META_DATA_ATTR_VALUE",
            (("COLL_NAME = '%s' AND DATA_NAME = 'datarequest.json' AND " +
             "META_DATA_ATTR_NAME = 'assignedForReview'") %
             (collName)).format(clientZone),
            irods_types.GenQueryInp())
        query = ret_val["arguments"][2]
        ret_val = callback.msiExecGenQuery(query, irods_types.GenQueryOut())
        while True:
            result = ret_val["arguments"][1]
            for row in range(result.rowCnt):
                reviewers.append(result.sqlResult[0].row(row))

            if result.continueInx == 0:
                break
            ret_val = callback.msiGetMoreRows(query, result, 0)
        callback.msiCloseGenQuery(query, result)

        # ... then removing the current reviewer from the list
        reviewers.remove(clientName)

        # ... and then updating the assignedForReview attributes
        status = ""
        statusInfo = ""
        callback.requestDatarequestMetadataChange(collName,
                                                  "assignedForReview",
                                                  json.dumps(reviewers),
                                                  str(len(
                                                      reviewers)),
                                                  status, statusInfo)
        callback.adminDatarequestActions()

        # If there are no reviewers left, change the status of the proposal to
        # 'reviewed' and send an email to the board of directors members
        # informing them that the proposal is ready to be evaluated by them.
        if len(reviewers) < 1:
            status = ""
            statusInfo = ""
            callback.requestDatarequestMetadataChange(collName, "status",
                                                      "reviewed", "", status,
                                                      statusInfo)
            callback.adminDatarequestActions()

            # Get parameters needed for sending emails
            researcherName  = ""
            researcherEmail = ""
            bodmemberEmails = ""
            rows = row_iterator(["META_DATA_ATTR_NAME", "META_DATA_ATTR_VALUE"],
                                ("COLL_NAME = '%s' AND " +
                                 "DATA_NAME = '%s'") % (collPath,
                                                        'datarequest.json'),
                                AS_DICT,
                                callback)
            for row in rows:
                name  = row["META_DATA_ATTR_NAME"]
                value = row["META_DATA_ATTR_VALUE"]
                if name == "name":
                    researcherName  = value
                elif name == "email":
                    researcherEmail = value
            bodmemberEmails = json.loads(callback.uuGroupGetMembersAsJson(
                                             'datarequests-research-board-of-directors',
                                             bodmemberEmails)['arguments'][1])

            # Send email to researcher and data manager notifying them of the
            # submission of this data request
            sendMail(researcherEmail, "[researcher] YOUth data request %s: reviewed" % requestId, "Dear %s,\n\nYour data request been reviewed by the YOUth data management committee and is awaiting final evaluation by the YOUth Board of Directors.\n\nThe following link will take you directly to your data request: https://portal.yoda.test/datarequest/view/%s.\n\nWith kind regards,\nYOUth" % (researcherName, requestId))
            for bodmemberEmail in bodmemberEmails:
                if not bodmemberEmail == "rods":
                    sendMail(bodmemberEmail, "[bod member] YOUth data request %s: reviewed" %requestId, "Dear Board of Directors member,\n\nData request %s has been reviewed by the YOUth data management committee and is awaiting your final evaluation.\n\nPlease log into Yoda to evaluate the data request.\n\nThe following link will take you directly to the evaluation form: https://portal.yoda.test/datarequest/evaluate/%s.\n\nWith kind regards,\nYOUth" % (requestId, requestId))

        status = 0
        statusInfo = "OK"
    except:
        pass

    return {'status': status, 'statusInfo': statusInfo}


# \brief Retrieve a data request review
#
# \param[in] requestId Unique identifier of the data request
#
def getReview(callback, requestId):
    status = -1
    statusInfo = "Internal server error"

    try:
        # Construct filename
        collName = '/tempZone/home/datarequests-research/' + requestId
        fileName = 'review_dmcmember.json'

        # Get the size of the review JSON file and the review's status
        results = []
        rows = row_iterator(["DATA_SIZE", "DATA_NAME", "COLL_NAME"],
                            ("COLL_NAME = '%s' AND " +
                             "DATA_NAME like '%s'") % (collName, fileName),
                            AS_DICT,
                            callback)
        for row in rows:
            collName = row['COLL_NAME']
            dataName = row['DATA_NAME']
            dataSize = row['DATA_SIZE']

        # Construct path to file
        filePath = collName + '/' + dataName

        # Get the contents of the review JSON file
        ret_val = callback.msiDataObjOpen("objPath=%s" % filePath, 0)
        fileDescriptor = ret_val['arguments'][1]
        ret_val = callback.msiDataObjRead(fileDescriptor, dataSize,
                                          irods_types.BytesBuf())
        fileBuffer = ret_val['arguments'][2]
        callback.msiDataObjClose(fileDescriptor, 0)
        reviewJSON = ''.join(fileBuffer.buf)

        status = 0
        statusInfo = "OK"
    except:
        reviewJSON = ""

    return {'reviewJSON': reviewJSON, 'status': status,
            'statusInfo': statusInfo}


# \brief Persist an evaluation to disk.
#
# \param[in] data       JSON-formatted contents of the evaluation
# \param[in] proposalId Unique identifier of the research proposal
#
def submitEvaluation(callback, data, requestId, rei):
    status = -1
    statusInfo = "Internal server error"

    try:
        # Check if user is a member of the Board of Directors. If not, do not
        # allow submission of the evaluation
        isBoardMember = False
        name = ""
        isBoardMember = groupUserMember("datarequests-research-board-of-directors",
                                        callback.uuClientFullNameWrapper(name)
                                            ['arguments'][0],
                                        callback)
        if not isBoardMember:
            status = -2
            statusInfo = "User is not a member of the Board of Directors."
            raise Exception()

        # Construct path to collection of the evaluation
        zonePath = '/tempZone/home/datarequests-research/'
        collPath = zonePath + requestId

        # Get username
        name = ""
        clientName = callback.uuClientNameWrapper(name)['arguments'][0]

        # Write evaluation data to disk
        reviewPath = collPath + '/evaluation_' + clientName + '.json'
        ret_val = callback.msiDataObjCreate(reviewPath, "", 0)
        fileDescriptor = ret_val['arguments'][2]
        callback.msiDataObjWrite(fileDescriptor, data, 0)
        callback.msiDataObjClose(fileDescriptor, 0)

        # Update the status of the data request to "approved"
        status = ""
        statusInfo = ""
        callback.requestDatarequestMetadataChange(collPath, "status",
                                                  "approved", 0,
                                                  status, statusInfo)
        callback.adminDatarequestActions()

        # Get parameters needed for sending emails
        researcherName  = ""
        researcherEmail = ""
        datamanagerEmails = ""
        rows = row_iterator(["META_DATA_ATTR_NAME", "META_DATA_ATTR_VALUE"],
                            ("COLL_NAME = '%s' AND " +
                             "DATA_NAME = '%s'") % (collPath,
                                                    'datarequest.json'),
                            AS_DICT,
                            callback)
        for row in rows:
            name  = row["META_DATA_ATTR_NAME"]
            value = row["META_DATA_ATTR_VALUE"]
            if name == "name":
                researcherName  = value
            elif name == "email":
                researcherEmail = value
        datamanagerEmails = json.loads(callback.uuGroupGetMembersAsJson('datarequests-research-datamanagers', datamanagerEmails)['arguments'][1])

        # Send an email to the researcher informing them of whether their data
        # request has been approved or rejected.
        evaluation = "approved"
        if evaluation == "approved":
            sendMail(researcherEmail, "[researcher] YOUth data request %s: approved" % requestId, "Dear %s,\n\nCongratulations! Your data request has been approved. The YOUth data manager will now create a Data Transfer Agreement for you to sign. You will be notified when it is ready.\n\nThe following link will take you directly to your data request: https://portal.yoda.test/datarequest/view/%s.\n\nWith kind regards,\nYOUth" % (researcherName, requestId))
            for datamanagerEmail in datamanagerEmails:
                if not datamanagerEmail == "rods":
                    sendMail("j.j.zondergeld@uu.nl", "[data manager] YOUth data request %s: approved" % requestId, "Dear data manager,\n\nData request %s has been approved by the Board of Directors. Please sign in to Yoda to upload a Data Transfer Agreement for the researcher.\n\nThe following link will take you directly to the data request: https://portal.yoda.test/view/%s.\n\nWith kind regards,\nYOUth" % (requestId, requestId))
        elif evaluation == "rejected":
            sendMail(researcherEmail, "[researcher] YOUth data request %s: rejected" % requestId, "Dear %s,\n\nYour data request has been rejected. Please log in to Yoda to view additional details.\n\nThe following link will take you directly to your data request: https://portal.yoda.test/datarequest/view/%s.\n\nIf you wish to object against this rejection, please contact the YOUth data manager (%s).\n\nWith kind regards,\nYOUth" % (researcherName, requestId, datamanagerEmail[0]))

        status = 0
        statusInfo = "OK"
    except:
        pass

    return {'status': status, 'statusInfo': statusInfo}


# \brief Set the status of a submitted datarequest to "approved"
#
# \param[in] requestId        Unique identifier of the datarequest.
# \param[in] currentUserName  Username of the user whose ownership is checked.
#
def approveRequest(callback, requestId, currentUserName):
    status = -1
    statusInfo = "Internal server error"

    try:
        # Check if approving user owns the datarequest. If so, do not allow
        # approving
        result = isRequestOwner(callback, requestId, currentUserName)
        if result['isRequestOwner']:
            raise Exception()

        # Construct path to the collection of the datarequest
        zoneName = ""
        clientZone = callback.uuClientZone(zoneName)['arguments'][0]
        requestColl = ("/" + clientZone + "/home/datarequests-research/" +
                        requestId)

        # Approve the datarequest by adding a delayed rule that sets the status
        # of the datarequest to "approved" ...
        status = ""
        statusInfo = ""
        callback.requestDatarequestMetadataChange(requestColl, "status",
                                               "approved", 0, status, statusInfo)

        # ... and triggering the processing of delayed rules
        callback.adminDatarequestActions()

        # Set status to OK
        status = 0
        statusInfo = "OK"
    except:
        pass

    return {'status': status, 'statusInfo': statusInfo}


def uuAssignRequest(rule_args, callback, rei):
    callback.writeString("stdout", json.dumps(assignRequest(callback,
                                                            rule_args[0], rule_args[1])))


def uuApproveRequest(rule_args, callback, rei):
    callback.writeString("stdout", json.dumps(approveRequest(callback,
                                                  rule_args[0], rule_args[1])))


def uuIsRequestOwner(rule_args, callback, rei):
    callback.writeString("stdout", json.dumps(isRequestOwner(callback,
                                                  rule_args[0], rule_args[1])))


def uuSubmitDatarequest(rule_args, callback, rei):
    callback.writeString("stdout", json.dumps(submitDatarequest(callback,
                                                                rule_args[0], rei)))


def uuGetDatarequest(rule_args, callback, rei):
    callback.writeString("stdout", json.dumps(getDatarequest(callback,
                                                             rule_args[0])))
