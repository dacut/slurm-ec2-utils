Summary:            An open-source workload manager designed for Linux clusters of all sizes.
Name:               slurm
Version:            14.11.1
Release:            1%{?dist}
License:            GPLv2+
Group:              System Tools
Source:             http://www.schedmd.com/download/latest/%{name}-%{version}.tar.bz2
URL:                http://www.schedmd.com/
Requires(pre):      /bin/egrep /usr/sbin/useradd
Requires(post):     /sbin/chkconfig /sbin/service
Requires(post):     /sbin/service
Requires:           glib2 hwloc munge ncurses openssl rrdtool
BuildRequires:      gcc gcc-c++ glib2-devel hwloc-devel munge ncurses-devel openssl-devel rrdtool-devel

%description
SLURM is an open-source resource manager designed for Linux clusters
of all sizes. It provides three key functions. First it allocates
exclusive and/or non-exclusive access to resources (computer nodes) to
users for some duration of time so they can perform work. Second, it
provides a framework for starting, executing, and monitoring work
(typically a parallel job) on a set of allocated nodes. Finally, it
arbitrates contention for resources by managing a queue of pending
work.

%pre
/bin/egrep '^slurm:' /etc/passwd > /dev/null || /usr/sbin/useradd --comment "Simple Linux Utility for Resource Management" --system --shell /sbin/nologin --home /var/slurm slurm

%post
/sbin/chkconfig --add slurm
[[ -r /etc/slurm.conf ]] && /sbin/service slurm start

%preun
[[ -r /etc/slurm.conf ]] && /sbin/service slurm stop

%prep
%setup -q -n %{name}-%{version}
%build
%configure

# Remove -rpath flags from libtool
cp libtool libtool.orig
sed -i 's|^hardcode_libdir_flag_spec=.*|hardcode_libdir_flag_spec=""|g' libtool
sed -i 's|^runpath_var=LD_RUN_PATH|runpath_var=DIE_RPATH_DIE|g' libtool

# And from Makefiles
find . -name Makefile -exec sed -i -e 's!\(\(HWLOC\|MUNGE\|RRDTOOL\)_LDFLAGS\) = -Wl,-rpath -Wl,/usr/lib\(\|32\|64\)!\1 =!' {} ';'

make %{?_smp_mflags}

%install
pwd
rm -rf %{buildroot}
%make_install
# slurm's init.d scripts appear to have missing substitutions.
pwd
mkdir -p %{buildroot}/etc/init.d
for initfile in slurm slurmdbd; do
    sed -e 's/\${exec_prefix}/\/usr/g' etc/init.d.$initfile > %{buildroot}/etc/init.d/$initfile
    chmod 755 %{buildroot}/etc/init.d/$initfile
done;


%files
%doc AUTHORS ChangeLog COPYING DISCLAIMER INSTALL LICENSE.OpenSSL META NEWS README.rst RELEASE_NOTES
/usr/share/doc/%{name}-%{version}
%{_sysconfdir}/init.d/slurm
%{_sysconfdir}/init.d/slurmdbd
%{_bindir}/*
%{_sbindir}/*
%{_libdir}/*
%{_includedir}/*
%{_mandir}/man1/*
%{_mandir}/man3/*
%{_mandir}/man5/*
%{_mandir}/man8/*
