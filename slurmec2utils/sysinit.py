#!/usr/bin/python
from __future__ import absolute_import, print_function
import boto.s3
from boto.s3.key import Key
from .clusterconfig import ClusterConfiguration
from .instanceinfo import get_instance_id

def get_munge_key(cluster_configuration=None):
    if cluster_configuration is None:
        cluster_configuration = ClusterConfiguration()
