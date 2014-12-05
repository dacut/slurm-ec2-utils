#!/usr/bin/python
from __future__ import absolute_import, print_function
from base64 import b64encode
import boto.ec2
from boto.ec2.blockdevicemapping import BlockDeviceMapping, BlockDeviceType
from boto.ec2.networkinterface import (
    NetworkInterfaceCollection, NetworkInterfaceSpecification)
from .clusterconfig import ClusterConfiguration
from .instanceinfo import get_instance_id, get_region, get_vpc_id
import sys
from sys import argv
from time import gmtime, sleep, strftime, time

amazon_linux_ami = {
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

init_script = """\
#!/bin/sh
hostname '%(nodename)s'
instance_id=`curl --silent http://169.254.169.254/latest/meta-data/instance-id`
aws --region %(region)s ec2 create-tags --resources $instance_id --tags \
'Key=SLURMHostname,Value=%(nodename)s' \
'Key=SLURMS3Root,Value=%(slurm_s3_root)s' \
'Key=Name,Value=SLURM Computation Node %(nodename)s'
cat > /etc/slurm-ec2.conf <<.EOF
%(slurm_ec2_conf)s
.EOF
if [[ ! -z "%(os_packages)s" ]]; then
    yum -y install %(os_packages)s;
fi;

for package in %(external_packages)s; do
    tmpdir=`mktemp -d`
    aws s3 cp %(slurm_s3_root)s/packages/$package $tmpdir/$package
    case $package in
        *.rpm )
            rpm --install $tmpdir/$package;;

        *.tgz | *.tar.gz )
            tar -C / -x -z -f $tmpdir/$package;;

        *.tbz2 | *.tar.bz2 )
            tar -C / -x -j -f $tmpdir/$package;;

        *.tZ | *.tar.Z )
            tar -C / -x -Z -f $tmpdir/$package;;

        * )
            chmod 755 $tmpdir/$package;
            mv $tmpdir/$package /usr/bin;;
    esac;

    rm -rf $tmpdir;
done

aws s3 cp %(slurm_s3_root)s/packages/slurm-ec2-bootstrap \
/usr/bin/slurm-ec2-bootstrap
chmod 755 /usr/bin/slurm-ec2-bootstrap
/usr/bin/slurm-ec2-bootstrap --slurm-s3-root '%(slurm_s3_root)s'
"""

def start_logging():
    fd = open("/var/log/slurm/slurm-ec2-powersave.log", "a")
    sys.stdout = sys.stderr = fd

def start_node():
    start_logging()

    print(" ".join(argv))

    if len(argv) != 2:
        print("Usage: %s <nodename>" % (argv[0],), file=sys.stderr)
        return 1

    nodename = argv[1]

    cc = ClusterConfiguration.from_config()
    region = get_region()
    ec2 = boto.ec2.connect_to_region(region)
    
    if not ec2:
        print("Could not connect to EC2 endpoint in region %r" % (region,),
              file=sys.stderr)
        return 1

    kw = {}
    slurm_s3_root = cc.slurm_s3_root

    kw['image_id'] = (
        cc.compute_ami if cc.compute_ami is not None
        else amazon_linux_ami[region])
    if cc.instance_profile is not None:
        if cc.instance_profile.startswith("arn:"):
            kw['instance_profile_arn'] = cc.instance_profile
        else:
            kw['instance_profile_name'] = cc.instance_profile
    kw['key_name'] = cc.key_name
    kw['instance_type'] = cc.compute_instance_type

    if cc.compute_bid_price is not None:
        end = time() + 24 * 60 * 60  # FIXME: Don't hardcode this.
        kw['price'] = cc.compute_bid_price
        kw['valid_until'] = strftime("%Y-%m-%dT%H:%M:%SZ", gmtime(end))
    
    node_address = cc.get_address_for_nodename(nodename)
    node_subnet = cc.get_subnet_for_address(node_address)
    user_data = init_script % {
        "region": region,
        "nodename": nodename,
        "os_packages": " ".join(
            cc.compute_os_packages
            if cc.compute_os_packages is not None
            else []),
        "external_packages": " ".join(
            cc.compute_external_packages
            if cc.compute_external_packages is not None
            else []),
        "slurm_ec2_conf": cc.slurm_ec2_configuration,
        "slurm_s3_root": slurm_s3_root,
    }
    user_data = b64encode(user_data)
    kw['user_data'] = user_data

    # Map the ethernet interface to the correct IP address
    eth0 = NetworkInterfaceSpecification(
        associate_public_ip_address=True,
        delete_on_termination=True,
        device_index=0,
        groups=cc.security_groups,
        private_ip_address=str(node_address),
        subnet_id=node_subnet.id)

    kw['network_interfaces'] = NetworkInterfaceCollection(eth0)

    # Attach any ephemeral storage devices
    block_device_map = BlockDeviceMapping()
    block_device_map['/dev/xvda'] = BlockDeviceType(size=32, volume_type="gp2")
    devices = cc.ephemeral_stores[cc.compute_instance_type]

    for i, device in enumerate(devices):
        drive = "/dev/sd" + chr(ord('b') + i)
        block_device_map[drive] = BlockDeviceType(
            ephemeral_name="ephemeral%d" % i)

    kw['block_device_map'] = block_device_map

    if cc.compute_bid_price is None:
        print("run_instances: %r" % kw)
        reservation = ec2.run_instances(**kw)
        tags = {
            'SLURMHostname': nodename,
            'SLURMS3Root': slurm_s3_root,
            'Name': "SLURM Computation Node %s" % nodename,
        }

        print("instances: %s" %
              " ".join([instance.id for instance in reservation.instances]))

        # create-tags can fail at times since the tag resource database is
        # a bit behind EC2's actual state.
        for i in xrange(10):
            try:
                ec2.create_tags([
                    instance.id for instance in reservation.instances], tags)
                break
            except Exception as e:
                print("Failed to tag instance: %s" % e, file=sys.stderr)
                sleep(0.5 * i)
    else:
        print("request_spot_instances: %r" % kw, file=sys.stderr)
        requests = ec2.request_spot_instances(**kw)
        print("requests: %s" % " ".join([request.id for request in requests]))

    return 0

def stop_node():
    start_logging()

    print(" ".join(argv))

    if len(argv) != 2:
        print("Usage: %s <nodename>" % (argv[0],), file=sys.stderr)
        return 1

    nodename = argv[1]

    cc = ClusterConfiguration.from_config()
    region = get_region()
    ec2 = boto.ec2.connect_to_region(region)

    instances = ec2.get_only_instances(filters={"tag:SLURMHostname": nodename})
    if len(instances) == 0:
        print("No instances found for %r" % nodename)
        return 1

    instance_ids = [instance.id for instance in instances]
    print("Terminating instance(s): %s" % " ".join(instance_ids))
    ec2.terminate_instances(instance_ids)
    return 0

