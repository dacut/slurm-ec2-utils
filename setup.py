from setuptools import setup

setup(
    name="slurm-ec2-utils",
    version="0.1",
    packages=["slurmec2utils"],
    entry_points={
        'console_scripts': [
            "slurm-ec2-clusterconfig=slurmec2utils.clusterconfig:main",
            "slurm-ec2-fallback-slurm-s3-root=slurmec2utils.clusterconfig:get_fallback_slurm_s3_root",
            "slurm-ec2-resume=slurmec2utils.powersave:start_node",
            "slurm-ec2-suspend=slurmec2utils.powersave:stop_node",
        ],
    },
    install_requires=["boto>=2.0", "netaddr>=0.7",],

    # PyPI information
    author="David Cuthbert",
    author_email="dacut@kanga.org",
    description="SLURM utilities for running on EC2",
    license="Apache License 2.0",
    keywords="slurm",
    url="s3://cuthbert-vbl/",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Other Scripting Engines",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Unix Shell",
        "Topic :: Scientific/Engineering",
        "Topic :: Utilities",
    ],
)
