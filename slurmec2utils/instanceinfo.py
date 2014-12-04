#!/usr/bin/python
from __future__ import absolute_import, print_function
import boto.ec2 
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

def get_instance():
    """
    Returns the boto.ec2.Instance object for this instance.
    """
    global _instance
    global _instance
    try:
        return _instance
    except NameError:
        region = get_region()
        instance_id = get_instance_id()
        ec2 = boto.ec2.connect_to_region(region)
        if ec2 is None:
            raise ValueError("Unable to connect to EC2 endpoint in region %r" %
                             (region,))

        instances = ec2.get_only_instances([instance_id])
        if len(instances) == 0:
            raise ValueError("Could not find instance id %r" %
                             (instance_id,))
        if len(instances) > 1:
            raise ValueError("Multiple instances returned for instance id %r" %
                             (instance_id,))
            
        _instance = instances[0]
        return _instance
