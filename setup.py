from setuptools import setup

setup(
    name="slurm-ec2-utils",
    version="0.1",
    packages=["slurmec2utils"],
    entry_points={
        'console_scripts': [
            "slurm-ec2-clusterconfig=slurmec2utils.clusterconfig:main",
        ],
    },
    install_requires=["boto>=2.0", "netaddr>=0.7",],

    # PyPI information
    author="David Cuthbert",
    author_email="dacut@kanga.org",
    description="SLURM utilities for running on EC2",
    license="BSD",
    keywords="slurm",
)
