# coding: utf-8

from setuptools import setup, find_packages
from pip.req import parse_requirements

setup(
    name='transit_vpc_push_pan_config',
    version='2.0',
    description='AWS Transit VPC Push Palo Alto Config',
    author='AWS Solutions Builder - mod by Brantley Richbourg for Palo Alto',
    license='ASL',
    zip_safe=False,
    packages=['transit_vpc_push_pan_config'],
    package_dir={'transit_vpc_push_pan_config': '.'},
    install_requires=[
        'paramiko>=1.16.0',
        'transit_vpc_push_pan_config>=2.0'
    ],
    classifiers=[
        'Programming Language :: Python :: 2.7',
    ],
)
