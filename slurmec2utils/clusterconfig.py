#!/usr/bin/python
from __future__ import absolute_import, print_function
from .instanceinfo import get_instance_id, get_instance, get_region, get_vpc_id
import boto.ec2
import boto.vpc
from boto.vpc.subnet import Subnet
from ConfigParser import DEFAULTSECT, RawConfigParser
try: from cStringIO import StringIO
except ImportError: from StringIO import StringIO
from math import floor, log10
from netaddr import IPAddress, IPNetwork
from sys import argv, exit, stderr, stdout
from types import NoneType

def get_fallback_slurm_s3_root(region=None):
    """
    Get the SLURM S3 root by examining the tags applied to the instance.
    This is used if the node has not (yet) been configured with an
    /etc/slurm-ec2.conf file (the bootstrapping problem).
    """
    if region is None:
        region = get_region()
    instance_id = get_instance_id()
    ec2 = boto.ec2.connect_to_region(region)
    if ec2 is None:
        raise ValueError("Unable to connect to EC2 endpoint in region %r" %
                         (region,))
    
    tags = ec2.get_all_tags(filters={"resource-type": "instance",
                                     "resource-id": instance_id,
                                     "key": "SLURMEC2Root"})
    if len(tags) == 0:
        return None
    
    return tags[0].value

class ClusterConfiguration(object):
    """
    Configure resources for SLURM operation.

    ClusterConfiguration objects are capable of creating the following files:
        /etc/slurm-ec2.conf - slurm-ec2-utils configuration file.
        /etc/slurm.conf - The SLURM configuration file.
        /etc/hosts - Host aliases.

    In addition, they are able to read the /etc/slurm-ec2.conf file to
    regenerate the other files.
    """

    # SLURM features per instance type
    slurm_features = {
        "t2.micro": "CoresPerSocket=1 Feature=burst RealMemory=1024",
        "t2.small": "CoresPerSocket=1 Feature=burst RealMemory=2048",
        "t2.medium": "CoresPerSocket=2 Feature=burst RealMemory=4096",
        "m3.medium": "CoresPerSocket=1 RealMemory=3840",
        "m3.large": "CoresPerSocket=1 ThreadsPerCore=2 RealMemory=7680",
        "m3.xlarge": "CoresPerSocket=2 ThreadsPerCore=2 RealMemory=15360",
        "m3.2xlarge": "CoresPerSocket=4 ThreadsPerCore=2 RealMemory=30720",
        "c3.large": "CoresPerSocket=1 ThreadsPerCore=2 RealMemory=3840",
        "c3.xlarge": "CoresPerSocket=2 ThreadsPerCore=2 RealMemory=7680",
        "c3.2xlarge": "CoresPerSocket=4 ThreadsPerCore=2 RealMemory=15360",
        "c3.4xlarge": "CoresPerSocket=8 ThreadsPerCore=2 RealMemory=30720",
        "c3.8xlarge": "CoresPerSocket=16 ThreadsPerCore=2 RealMemory=61440",
        "r3.large": "CoresPerSocket=1 ThreadsPerCore=2 RealMemory=15616",
        "r3.xlarge": "CoresPerSocket=2 ThreadsPerCore=2 RealMemory=31232",
        "r3.2xlarge": "CoresPerSocket=4 ThreadsPerCore=2 RealMemory=62464",
        "r3.4xlarge": "CoresPerSocket=8 ThreadsPerCore=2 RealMemory=124928",
        "r3.8xlarge": "CoresPerSocket=16 ThreadsPerCore=2 RealMemory=249856",
        "g2.2xlarge": "CoresPerSocket=4 ThreadsPerCore=2 RealMemory=15360",
        "i2.xlarge": "CoresPerSocket=2 ThreadsPerCore=2 RealMemory=31232",
        "i2.2xlarge": "CoresPerSocket=4 ThreadsPerCore=2 RealMemory=62464",
        "i2.4xlarge": "CoresPerSocket=8 ThreadsPerCore=2 RealMemory=124928",
        "i2.8xlarge": "CoresPerSocket=16 ThreadsPerCore=2 RealMemory=249856",
        "hs1.8xlarge": "CoresPerSocket=8 ThreadsPerCore=2 RealMemory=119808",
    }

    # Ephemeral storage volume sizes (in GB) per instance type
    ephemeral_stores = {
        "t2.micro":     [],
        "t2.small":     [],
        "t2.medium":    [],
        "m3.medium":    [4],
        "m3.large":     [32],
        "m3.xlarge":    [40] * 2,
        "m3.2xlarge":   [80] * 2,
        "c3.large":     [16] * 2,
        "c3.xlarge":    [40] * 2,
        "c3.2xlarge":   [80] * 2,
        "c3.4xlarge":   [160] * 2,
        "c3.8xlarge":   [320] * 2,
        "r3.large":     [32],
        "r3.xlarge":    [80],
        "r3.2xlarge":   [160],
        "r3.4xlarge":   [320],
        "r3.8xlarge":   [320] * 2,
        "g2.2xlarge":   [60],
        "i2.xlarge":    [800],
        "i2.2xlarge":   [800] * 2,
        "i2.4xlarge":   [800] * 4,
        "i2.8xlarge":   [800] * 8,
        "hs1.8xlarge":  [2000] * 24,
    }

    # Default values for the parser and constructor.
    defaults = {
        'region': None,
        'slurm_s3_root': None,
        'vpc_id': None,
        'instance_profile': None,
        'key_name': None,
        'security_groups': None,
        'node_subnet_ids': None,
        'controller_address': None,
        'backup_controller_address': None,
        'controller_hostname': "controller",
        'backup_controller_hostname': "backup-controller",
        'node_hostname_prefix': "node-",
        'reserved_addresses': 8,
        'max_nodes': None,
        'compute_instance_type': "c3.8xlarge",
        'compute_ami': None,
        'compute_bid_price': None,
        'compute_os_packages': None,
        'compute_external_packages': None,
        'app_config': None,
    }

    # Keys which are lists in the slurm-ec2 config section.
    list_keys = {'security_groups', 'node_subnet_ids', 'compute_os_packages',
                 'compute_external_packages'}

    # Keys which are integers in the slurm-ec2 config section
    int_keys = {'reserved_addresses', 'max_nodes'}

    # Master configuration section name
    master_config_section = "slurm-ec2"

    def __init__(self, **kw):
        """
        ClusterConfiguration(
            region=None, slurm_s3_root=None, vpc_id=None, key_name=None,
            node_subnet_ids=None, controller_address=None,
            backup_controller_address=None, controller_hostname="controller",
            backup_controller_hostname="backup-controller",
            node_hostname_prefix="node-", reserved_addresses=8,
            max_nodes=65535, compute_instance_type="c3.8xlarge",
            compute_ami=None, compute_bid_price=None, compute_os_packages=None,
            compute_external_packages=None, app_config=None)

        Create a ClusterConfiguration object.

        region specifies the AWS region to use for querying AWS resources.
        If None, the region of the current instance is used.

        slurm_s3_root specifies the S3 bucket and optional prefix (in
        s3://<bucket>/<prefix> format) to use for downloading configuration
        data from S3.

        vpc_id specifies the Virtual Private Cloud (VPC) to configure.
        If None, the VPC of the current instance is used.

        key_name specifies the SSH key to apply to compute instances.  If
        None, the key used for the controller is used.

        node_subnet_ids specifies the subnets in the VPC to use for SLURM
        computation nodes.  If None, all subnets in the VPC are used.

        controller_address specifies the address to use for the SLURM
        controller.  If None or "auto", the lowest VPC subnet CIDR base is
        taken and an offset of 4 is used -- e.g. if the lowest CIDR is
        192.168.0.0, the controller is assigned 192.168.0.4.  Note that AWS
        reserves the first three addresses at the base of each subnet.

        backup_controller_address specifies the address to use for the SLURM
        backup controller.  If None, a backup controller is not used.
        If this is set to "auto", the lowest VPC subnet in a different
        availability zone from the primary controller is taken and an offset
        of 4 is used.

        controller_hostname and backup_controller_hostname are the hostnames
        to use for the primary and backup controllers.

        node_hostname_prefix is the hostname prefix to use for computation
        nodes.

        reserved_addresses specifies how many addresses in each subnet are to
        be reserved for non-compute purposes.

        max_nodes specifies the maximum number of SLURM computation nodes.
        This is limited to 65531 (the size of a /16 - 3 (AWS reserved
        addresses) - 1 (SLURM controller)).

        compute_instance_type specifies the EC2 instance type to use.
        This defaults to c3.8xlarge.

        compute_ami specifies the Amazon Machine Image (AMI) id to use.
        If None, this defaults to the Amazon Linux AMI in the region.

        compute_bid_price specifies the bid price for requesting spot
        instances.  If None, on-demand instances are used.

        compute_os_packages specifies the names of packages provided by the OS
        vendor to install on compute nodes.  These are installed via
        "yum install" (RedHat variants) or "apt-get install" (Debian variants).

        compute_external_packages specifies the names of packages provided in
        slurm_s3_root (under rpm/RPMS or deb).  These are downloaded and
        installed via "rpm --install" (RedHat variants) or "dpkg --install"
        (Debian variants).

        app_config, if specified, is a two-level dictionary of configuration
        information for applications.  Top level keys are written to
        slurm-ec2.conf as sections; second level keys are configuration keys.
        """
        tmpkw = dict(self.defaults)
        tmpkw.update(kw)
        kw = tmpkw
        del tmpkw

        self.region = (
            kw['region'] if kw['region'] is not None else get_region())
        self.slurm_s3_root = (
            kw['slurm_s3_root'] if kw['slurm_s3_root'] is not None
            else get_fallback_slurm_s3_root(self.region))
        self.vpc_id = (
            kw['vpc_id'] if kw['vpc_id'] is not None else get_vpc_id())
        self.instance_profile = (
            kw['instance_profile'] if kw['instance_profile'] is not None
            else get_instance().instance_profile['arn'])
        self.key_name = (
            kw['key_name'] if kw['key_name'] is not None
            else get_instance().key_name)
        self.security_groups = (
            kw['security_groups'] if kw['security_groups'] is not None
            else [g.id for g in get_instance().groups])

        if kw.get('_all_subnets') is None:
            vpc_conn = boto.vpc.connect_to_region(self.region)
            if vpc_conn is None:
                raise ValueError("Cannot connect to AWS VPC endpoint in "
                                 "region %r" % self.region)
            self.all_subnets = vpc_conn.get_all_subnets(
                filters={'vpcId': self.vpc_id})
        else:
            self.all_subnets = kw['_all_subnets']

        self.node_subnets = [
            subnet for subnet in self.all_subnets
            if (kw.get('node_subnet_ids') is None or
                subnet.id in kw['node_subnet_ids'])]

        if kw['controller_address'] == "auto" or (
            isinstance(kw['controller_address'], (NoneType, IPAddress))):
            self._controller_address = kw['controller_address']
        else:
            self._controller_address = IPAddress(kw['controller_address'])

        if kw['backup_controller_address'] == "auto" or (
            isinstance(kw['backup_controller_address'],
                       (NoneType, IPAddress))):
            self._backup_controller_address = kw['backup_controller_address']
        else:
            self._backup_controller_address = IPAddress(
                kw['backup_controller_address'])

        self.controller_hostname = kw['controller_hostname']
        self.backup_controller_hostname = kw['backup_controller_hostname']
        self.node_hostname_prefix = kw['node_hostname_prefix']
        self.reserved_addresses = kw['reserved_addresses']
        self.max_nodes = kw['max_nodes']
        self.compute_instance_type = kw['compute_instance_type']
        self.compute_ami = (
            kw['compute_ami'] if kw['compute_ami'] is not None
            else get_instance().image_id)
        self.compute_bid_price = kw['compute_bid_price']
        self.compute_os_packages = kw['compute_os_packages']
        self.compute_external_packages = kw['compute_external_packages']
        self.app_config = kw['app_config']
        return

    @property
    def node_cidr_blocks(self):
        """
        A list of node subnet CIDR blocks (netattr.IPNetwork objects), sorted
        by CIDR.
        """
        node_cidr_blocks = [IPNetwork(subnet.cidr_block)
                            for subnet in self.node_subnets]
        node_cidr_blocks.sort()
        return node_cidr_blocks

    @property
    def controller_address(self):
        """
        The address of the controller node (netattr.IPAddress object).
        """
        if (self._controller_address is None or
            self._controller_address == "auto"):
            # Find the subnet with the lowest CIDR range.
            min_subnet = sorted(
                self.all_subnets,
                cmp=lambda x, y: cmp(IPNetwork(x.cidr_block),
                                     IPNetwork(y.cidr_block)))[0]
            return IPNetwork(min_subnet.cidr_block).network + 4
        return self._controller_address

    @property
    def controller_subnet(self):
        """
        The subnet of the controller node (boto.vpc.subnet.Subnet object).
        """
        return self.get_subnet_for_address(self.controller_address)

    @property
    def backup_controller_address(self):
        """
        The address of the backup controller node (netattr.IPAddress object)
        or None.
        """
        if self._backup_controller_address is None:
            return None
        if self._backup_controller_address == "auto":
            # Figure out which AZ the controller is in.
            controller_az = self.controller_subnet.availability_zone
            
            # Remove all subnets in the same AZ.
            other_az_subnets = [
                subnet for subnet in self.all_subnets
                if subnet.availability_zone != controller_az]

            if len(other_az_subnets) == 0:
                # No other AZ available.
                return None
            
            # Find the subnet with the lowest CIDR range.
            min_subnet = sorted(
                other_az_subnets,
                cmp=lambda x, y: cmp(IPNetwork(x.cidr_block),
                                     IPNetwork(y.cidr_block)))[0]
            return IPNetwork(min_subnet.cidr_block).network + 4
        
        return self._backup_controller_address

    @property
    def backup_controller_subnet(self):
        """
        The subnet of the backup controller node (boto.vpc.subnet.Subnet
        object) or None.
        """
        addr = self.backup_controller_address
        if addr is None:
            return None
        return self.get_subnet_for_address(addr)

    @property
    def node_addresses(self):
        """
        A list of all valid node addresses (list of netaddr.IPAddress objects).

        The resulting list is ordered so that the load is distributed across
        availability zones.
        """
        hosts_by_subnet = [
            list(IPNetwork(subnet.cidr_block).iter_hosts())[
                self.reserved_addresses:]
            for subnet in self.node_subnets]

        # Make sure each list has the same length; append Nones to subnets
        # which are short.
        max_length = max([len(host_list) for host_list in hosts_by_subnet])
        for host_list in hosts_by_subnet:
            host_list.extend([None] * (max_length - len(host_list)))

        # Then interweave the hosts in each subnet so we distribute the load
        # across AZs appropriately.
        hosts = []
        for host_tuple in zip(*hosts_by_subnet):
            for host in filter(None, host_tuple):
                hosts.append(host)
                if len(hosts) >= self.max_nodes:
                    break
        
            if len(hosts) >= self.max_nodes:
                break
            
        return hosts

    def get_address_for_nodename(self, nodename):
        """
        Return the address for the given nodename.
        """
        if not nodename.startswith(self.node_hostname_prefix):
            raise ValueError("Invalid node name %r" % (nodename,))
        
        node_id = int(nodename[len(self.node_hostname_prefix):])
        return self.node_addresses[node_id]

    @property
    def slurm_ec2_configuration(self):
        """
        Returns the contents for writing to /etc/slurm-ec2.conf.
        """

        # Note: We don't use a ConfigParser object here because its write()
        # method uses an unpredictable (hashtable) order for writing sections
        # and keys, making the resulting configuration hard for humans to
        # read.
        conf = StringIO()
        conf.write("[slurm-ec2]\n")

        for attr in ["region", "slurm_s3_root", "vpc_id", "instance_profile",
                     "key_name", "security_groups", "controller_address",
                     "backup_controller_address", "controller_hostname",
                     "backup_controller_hostname", "node_hostname_prefix",
                     "reserved_addresses", "max_nodes",
                     "compute_instance_type", "compute_ami",
                     "compute_bid_price", "compute_os_packages",
                     "compute_external_packages"]:
            value = getattr(self, attr)
            if value is None:
                pass
            elif isinstance(value, (list, tuple)):
                conf.write("%s=%s\n" % (attr, " ".join(
                    [str(el) for el in value])))
            else:
                conf.write("%s=%s\n" % (attr, value))
        conf.write("node_subnet_ids=%s\n" % " ".join(
            [subnet.id for subnet in self.node_subnets]))

        # Write out the VPC configuration
        conf.write("\n[%s]\n" % self.vpc_id)
        conf.write("subnet_ids=%s\n" % " ".join([
            subnet.id for subnet in self.all_subnets]))

        # Write out each subnet configuration
        for subnet in self.all_subnets:
            conf.write("\n[%s]\n" % subnet.id)
            conf.write("cidr_block=%s\n" % subnet.cidr_block)
            conf.write("availability_zone=%s\n" % subnet.availability_zone)

        # Write out any application-specific data.
        if self.app_config is not None:
            for appname, appdata in sorted(self.app_config.items()):
                conf.write("\n[%s]\n" % appname)
                
                for kv in sorted(appdata.items()):
                    conf.write("%s=%s\n" % kv)
            
        return conf.getvalue()

    @property
    def slurm_configuration(self):
        """
        Returns the desired SLURM configuration file (/etc/slurm.conf).
        """
        control = StringIO()
        control.write("ControlMachine=%s\n" % self.controller_hostname)
        control.write("ControlAddr=%s\n" % self.controller_address)

        backup_addr = self.backup_controller_address
        if backup_addr is not None:
            control.write("BackupController=%s\n" %
                         self.backup_controller_hostname)
            control.write("BackupAddr=%s\n" % backup_addr)

        control = control.getvalue().strip()
        features = self.slurm_features.get(self.compute_instance_type, "")
        
        node_addresses = self.node_addresses
        max_node = len(self.node_addresses) - 1
        return """
%(control)s
AuthType=auth/munge
CacheGroups=0
CryptoType=crypto/munge
ReturnToService=1
SlurmctldPidFile=/var/run/slurmctld.pid
SlurmctldPort=6817
SlurmdPidFile=/var/run/slurmd.pid
SlurmdPort=6818
SlurmdSpoolDir=/var/slurm/spool
SlurmUser=slurm
StateSaveLocation=/var/slurm/state
SwitchType=switch/none
TaskPlugin=task/none

# TIMERS
InactiveLimit=0
KillWait=30
MinJobAge=300
SlurmctldTimeout=120
SlurmdTimeout=300
Waittime=0

# SCHEDULING 
FastSchedule=1
SchedulerType=sched/backfill
SchedulerPort=7321
SelectType=select/linear
SuspendProgram=/usr/bin/slurm-ec2-suspend
ResumeProgram=/usr/bin/slurm-ec2-resume
SuspendTime=600
TreeWidth=65535

# LOGGING AND ACCOUNTING 
AccountingStorageType=accounting_storage/none
AccountingStoreJobComment=YES
ClusterName=cluster
JobCompType=jobcomp/none
JobAcctGatherFrequency=30
JobAcctGatherType=jobacct_gather/none
SlurmctldLogFile=/var/log/slurm/slurmctld.log
SlurmctldDebug=3
SlurmdLogFile=/var/log/slurm/slurmd.log
SlurmdDebug=3

# COMPUTE NODES
NodeName=%(node_hostname_prefix)s[0-%(max_node)d] Weight=1 Feature=cloud %(features)s State=CLOUD
PartitionName=cluster Nodes=node-[0-%(max_node)d] Default=yes
""" % {'control': control,
       'node_hostname_prefix': self.node_hostname_prefix,
       'max_node': max_node,
       'features': features}

    @property
    def hosts(self):
        """
        Returns the desired hosts configuration file.
        """
        hosts = StringIO()
        hosts.write("127.0.0.1 localhost localhost.localdomain\n")
        hosts.write("%s %s %s.%s.compute.internal\n" % (
            self.controller_address, self.controller_hostname,
            self.controller_hostname, self.region))
        backup_addr = self.backup_controller_address
        if backup_addr is not None:
            hosts.write("%s %s %s.%s.compute.internal\n" % (
                backup_addr, self.backup_controller_hostname,
                self.backup_controller_hostname, self.region))
        
        for i, addr in enumerate(self.node_addresses):
            hosts.write("%s %s%d %s%d.%s.compute.internal\n" % (
                addr, self.node_hostname_prefix, i,
                self.node_hostname_prefix, i, self.region))

        return hosts.getvalue()

    def get_subnet_for_address(self, addr):
        """
        cc.get_subnet_for_address(addr) -> boto.vpc.subnet.Subnet | None
        
        Returns the subnet containing the specified address.
        """
        if not isinstance(addr, IPAddress):
            addr = IPAddress(addr)

        for subnet in self.all_subnets:
            if addr in IPNetwork(subnet.cidr_block):
                return subnet
        return None

    @classmethod
    def from_config(cls, filename=None, fp=None):
        """
        ClusterConfiguration.from_config(filename=None, fp=None)

        Read the configuration from the specified filename or file handle.
        If neither are specified, /etc/slurm-ec2.conf is used.
        """
        cp = RawConfigParser()
        app_config_cp = RawConfigParser()

        # Set up defaults here so we don't need try/except blocks around every
        # cp.get() call below.
        for key, value in cls.defaults.iteritems():
            cp.set(DEFAULTSECT, key, value)

        if filename is None and fp is None:
            filename = "/etc/slurm-ec2.conf"

        if fp is not None:
            if filename is not None:
                raise ValueError("Cannot specify both filename and fp")
            cp.readfp(fp)
            app_config_cp.readfp(fp)
        else:
            cp.read(filename)
            app_config_cp.read(filename)

        # Utility for splitting a string into a list if present; returns
        # None if not present.
        def parse_list(value):
            if value is None:
                return None
            return value.split()

        # Utility for converting a string into an int if present; returns
        # None if not present
        def parse_int(value):
            if value is None:
                return None
            return int(value)

        kw = {}
        for key in cls.defaults.iterkeys():
            value = cp.get(cls.master_config_section, key)
            # Convert lists and integers
            if key in cls.list_keys:
                value = parse_list(value)
            elif key in cls.int_keys:
                value = parse_int(value)

            kw[key] = value

        if kw.get("vpc_id"):
            # Parse the VPC section; get the list of subnets it contains.
            vpc_id = kw['vpc_id']
            subnet_ids = parse_list(cp.get(vpc_id, "subnet_ids"))
            subnets = []

            # Parse each subnet and create a Boto subnet object without
            # querying the EC2 endpoint.
            for subnet_id in subnet_ids:
                cidr_block = cp.get(subnet_id, "cidr_block")
                availability_zone = cp.get(subnet_id, "availability_zone")

                subnet = Subnet()
                subnet.id = subnet_id
                subnet.vpc_id = vpc_id
                subnet.cidr_block = cidr_block
                subnet.available_ip_address_count = (
                    len(IPNetwork(cidr_block)) - 4)
                subnet.availability_zone = availability_zone
                subnets.append(subnet)

            kw["_all_subnets"] = subnets

        # We use the app_config_cp config parser here to avoid polluting
        # application config with our defaults.
        app_config = {}
        for appname in app_config_cp.sections():
            if (appname == "slurm-ec2" or appname.startswith("vpc-") or 
                appname.startswith("subnet-")):
                continue

            app_config[appname] = cp.items(appname)
        kw["app_config"] = app_config
            
        return cls(**kw)
        
    @staticmethod
    def get_hostname_for_address(addr):
        """
        get_hostname_for_address(addr) -> hostname

        Returns an EC2 generated hostname for the specified address.
        Warning: this assumes the current EC2 algorithm of taking an IPv4
        address in the form a.b.c.d and returning "ip-a-b-c-d".  This is not
        documented.
        """
        return "ip-" + str(addr).replace(".", "-")

def main():
    from sys import argv, stdout
    from argparse import ArgumentParser

    parser = ArgumentParser(
        description="Process slurm-ec2-utils configuration information")
    parser.add_argument(
        "--region", "-r",
        help=("The AWS region to use for querying AWS resources.  If "
              "unspecified, the region of the current instance is used."))
    parser.add_argument(
        "--slurm-s3-root", "-S",
        help=("The S3 URL prefix containing SLURM cloud resources."))
    parser.add_argument(
        "--vpc-id", "-v",
        help=("The Virtual Private Cloud (VPC) to configure.  If unspecified, "
              "the VPC of the current instance is used."))
    parser.add_argument(
        "--instance-profile", "-I",
        help=("The EC2 instance profile for launching new instances.  If "
              "unspecified, the instance profile of the current instance is "
              "used."))
    parser.add_argument(
        "--key-name", "-k",
        help=("The SSH key name to use for launching new instances.  If "
              "unspecified, the key name of the current instance is used."))
    parser.add_argument(
        "--security-groups", "--security-group", "-s", action="append",
        help=("The security groups to apply to new instances.  If "
              "unspecified, the security groups of the current instance are "
              "used"))
    parser.add_argument(
        "--node-subnet-ids", "--node-subnet-id", "-n", action="append",
        help=("The subnets in the VPC to use for SLURM computation nodes.  "
              "If unspecified, all subnets in the VPC are used."))
    parser.add_argument(
        "--controller-address", "-c",
        help=("The address to use for the SLURM controller.  If \"auto\" or "
              "unspecified, the lowest VPC subnet CIDR base is taken and an "
              "offset of 4 is used.  Note that AWS reserves the first three "
              "addresses at the base of each subnet."))
    parser.add_argument(
        "--backup-controller-address", "-b",
        help=("The address to use for the SLURM backup controller.  If "
              "unspecified, a backup controller is not used.  If this is set "
              "to \"auto\", the lowest VPC subnet in a different availability "
              "zone from the primary controller is taken and an offset of 4 "
              "is used."))
    parser.add_argument(
        "--controller-hostname", "-C", default="controller",
        help=("The hostname to use for the primary controller.  Defaults to "
              "\"controller\"."))
    parser.add_argument(
        "--backup-controller-hostname", "-B", default="backup-controller",
        help=("The hostname to use for the backup controller.  Defaults to "
              "\"backup-controller\"."))
    parser.add_argument(
        "--node-hostname-prefix", "-N", default="node-",
        help=("The hostname prefix to use for computation nodes."))
    parser.add_argument(
        "--reserved-addresses", "-R", type=int, default=8,
        help=("How many addresses in each subnet are to be reserved for "
              "non-compute purposes."))
    parser.add_argument(
        "--max-nodes", "-m", type=int, default=65535,
        help=("The maximum number of SLURM computation nodes."))
    parser.add_argument(
        "--compute-instance-type", "-i", default="c3.8xlarge",
        help=("The EC2 instance type to use for computation nodes.  This "
              "defaults to c3.8xlarge"))
    parser.add_argument(
        "--compute-ami", "-a",
        help=("The Amazon Machine Image (AMI) id to use for computation "
              "nodes.  This defaults to the image of the current instance."))
    parser.add_argument(
        "--compute-bid-price", "-p",
        help=("The bid price for requesting spot instances.  If unspecified, "
              "on-demand instances are used."))
    parser.add_argument(
        "--app-config", "-X", action='append', default=[],
        help=("Application-specific configuration in the form "
              "<app>:<key>=<value>."))
    parser.add_argument(
        "--config", "-f",
        help=("Read a slurm-ec2-utils configuration file for values."))
    parser.add_argument(
        "--write-slurm-config", action='append',
        help=("Write a slurm.conf file derived from the input parameters to "
              "the given file."))
    parser.add_argument(
        "--write-slurm-ec2-config", action='append', default=[],
        help=("Write a slurm-ec2.conf file derived from the input parameters "
              "to the given file."))
    parser.add_argument(
        "--write-hosts", action='append', default=[],
        help=("Write a hosts file derived from the input parameters to the "
              "given file."))

    ns = parser.parse_args()
    kw = vars(ns)

    # Flatten list keys
    for key in ClusterConfiguration.list_keys:
        if key in kw and kw[key] is not None:
            result = []
            for element in kw[key]:
                if " " in element:
                    result.extend([el.strip() for el in element.split()])
                elif "," in element:
                    result.extend([el.strip() for el in element.split(",")])
                else:
                    result.append(element)
            kw[key] = result
    
    config_filename = kw.pop("config", None)

    # Parse --app-config items.
    app_config_list = kw.pop("app_config")
    app_config = {}
    for item in app_config_list:
        if ":" not in item:
            continue
        app, keyvalue = item.split(":", 1)
        
        if "=" not in keyvalue:
            continue

        key, value = keyvalue.split("=", 1)
        app_dict = app_config.setdefault(app, {})
        app_dict[key] = value
    kw['app_config'] = app_config

    outputs = {
        "slurm_configuration": kw.pop("write_slurm_config", []),
        "slurm_ec2_configuration": kw.pop("write_slurm_ec2_config", []),
        "hosts": kw.pop("write_hosts", []),
    }

    if config_filename is not None:
        config = ClusterConfiguration.from_config(filename=config_filename)
    else:
        config = ClusterConfiguration(**kw)

    for attribute, filenames in outputs.iteritems():
        if filenames is None:
            continue

        data = getattr(config, attribute)
        
        for filename in filenames:
            if filename == "-":
                fd = stdout
            else:
                fd = open(filename, "w")

            fd.write(data)
        
            if fd is not stdout:
                fd.close()
            del fd
    
    return 0
