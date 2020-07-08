#!/usr/bin/env python
from setuptools import find_packages, setup

from util import __author__, __license__, __version__

# Get the long description from the README file
with open("README.md", "r") as fh:
    long_description = fh.read()

requirements = [
    'enum34==1.1.10',
    'Jinja2==2.10.1',
    'requests==2.21.0',
    'jsonschema==3.0.2',
    'pathvalidate==0.29.1',
    'xmltodict==0.12.0',
    'lxml==4.3.2',
    'requests_cache==0.5.2',
    'irods-avu-json==2.0.0',
]

setup(
    name="irods_util",
    version=__version__,
    author=__author__,
    description=(
        "Utilities for Yoda."
    ),
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/UtrechtUniversity/irods-ruleset-uu",
    license=__license__,
    packages=find_packages(),
    install_requires=requirements,
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)',
        'Operating System :: OS Independent',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.7',
    ],
    python_requires='>=2.7',
    project_urls={
        'Bug Reports': 'https://github.com/UtrechtUniversity/yoda/issues',
        'Source': 'https://github.com/UtrechtUniversity/irods-ruleset-uu',
    },
)
