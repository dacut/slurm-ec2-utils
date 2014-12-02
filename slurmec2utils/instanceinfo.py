#!/usr/bin/python
from __future__ import absolute_import, print_function
from boto.utils import get_instance_metadata

"""
Details about the current instance.

This caches the instance metadata so that the metadata endpoint is only
queried once.
"""

def get_metadata():
    """
    Returns the metadata information about the instance.
    """

    global _metadata
    try:
        return _metadata
    except NameError:
        _metadata = get_instance_metadata()
        return _metadata

def get_vpc_id():
    """
    Returns the VPC id that this instance is running in.
    """
    return (get_metadata()['network']['interfaces']['macs'].
            values()[0]['vpc-id'])

def get_availability_zone():
    """
    Returns the availability zone this instance is running in.
    """
    return get_metadata()['placement']['availability-zone']

def get_region():
    """
    Returns the region that the instance is running in.
    """
    return get_availability_zone()[:-1]

def get_instance_id():
    """
    Returns the instance id for this instance.
    """
    return get_metadata()['instance-id']
