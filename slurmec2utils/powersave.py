#!/usr/bin/python
from __future__ import absolute_import, print_function
from base64 import b64encode
import boto.ec2
from boto.ec2.blockdevicemapping import BlockDeviceMapping, BlockDeviceType
from boto.ec2.networkinterface import (
    NetworkInterfaceCollection, NetworkInterfaceSpecification)
from .clusterconfig import ClusterConfiguration
from .instanceinfo import get_instance_id, get_region, get_vpc_id
from sys import argv, stderr
from time import sleep

AMAZON_LINUX_AMI = {
    "ap-northeast-1":   "ami-4985b048",
    "ap-southeast-1":   "ami-ac5c7afe",
    "ap-southeast-2":   "ami-63f79559",
    "cn-north-1":       "ami-ce46d4f7",
    "eu-central-1":     "ami-b43503a9",
    "eu-west-1":        "ami-6e7bd919",
    "sa-east-1":        "ami-8737829a",
    "us-east-1":        "ami-b66ed3de",
    "us-west-1":        "ami-4b6f650e",
    "us-west-2":        "ami-b5a7ea85",
}

INIT_SCRIPT = """\
#!/bin/sh
mkdir -p /var/lib/slurm-ec2
aws s3 cp %(slurm_s3_root)s/packages/slurm-ec2-bootstrap \
/var/lib/slurm-ec2/slurm-ec2-bootstrap
chmod 755 /var/lib/slurm-ec2/slurm-ec2-bootstrap
/var/lib/slurm-ec2/slurm-ec2-bootstrap %(slurm_s3_root)s
"""

def start_node():
    if len(argv) != 2:
        print("Usage: %s <nodename>" % (argv[0],), file=stderr)
        return 1

    nodename = argv[1]

    cc = ClusterConfiguration.from_config()
    region = get_region()
    ec2 = boto.ec2.connect_to_region(region)
    
    if not ec2:
        print("Could not connect to EC2 endpoint in region %r" % (region,),
              file=stderr)
        return 1

    slurm_s3_root = cc.slurm_s3_root
    key_name = cc.key_name
    instance_type = cc.compute_instance_type
    ami = cc.compute_ami
    bid_price = cc.compute_bid_price
    node_address = cc.get_address_for_nodename(nodename)
    node_subnet = cc.get_subnet_for_address(node_address)
    
    if ami is None:
        ami = AMAZON_LINUX_AMI[region]

    user_data = INIT_SCRIPT % {"slurm_s3_root": slurm_s3_root}
    user_data = b64encode(user_data)

    tags = {
        "SLURMS3Root": slurm_s3_root,
        "Name": "SLURM Computation Node %s" % (nodename,),
        "SLURMHostname": nodename,
    }
    
    # Map the ethernet interface to the correct IP address
    eth0 = NetworkInterfaceSpecification(
        associate_public_ip_address=True,
        delete_on_termination=True,
        device_index=0,
        groups=["sg-545fcc31"],
        private_ip_address=str(node_address),
        subnet_id=node_subnet.id)

    network_interfaces = NetworkInterfaceCollection(eth0)

    # Attach any ephemeral storage devices
    block_device_map = BlockDeviceMapping()
    block_device_map['/dev/xvda'] = BlockDeviceType(size=32, volume_type="gp2")
    devices = cc.ephemeral_stores[instance_type]

    for i, device in enumerate(devices):
        drive = "/dev/sd" + chr(ord('b') + i)
        block_device_map[drive] = BlockDeviceType(
            ephemeral_name="ephemeral%d" % i)

    reservation = ec2.run_instances(
        image_id=ami, key_name=key_name, network_interfaces=network_interfaces,
        instance_type=instance_type, instance_profile_name="PowerUser",
        block_device_map=block_device_map, user_data=user_data)

    # create-tags can fail at times since the tag resource database is a bit
    # behind EC2's actual state.
    for i in xrange(10):
        try:
            ec2.create_tags([
                instance.id for instance in reservation.instances], tags)
            break
        except:
            sleep(0.5 * i)

    return 0

def stop_node():
    if len(argv) != 2:
        print("Usage: %s <nodename>" % (argv[0],), file=stderr)
        return 1

    nodename = argv[1]

    cc = ClusterConfiguration.from_config()
    region = get_region()
    ec2 = boto.ec2.connect_to_region(region)

    instances = ec2.get_only_instances(filters={"tag:SLURMHostname": nodename})
    ec2.terminate_instances([instance.id for instance in instances])
    return 0

