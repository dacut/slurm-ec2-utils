Summary:            An authentication service for creating and validating credentials
Name:               munge
Version:            0.5.11
Release:            1%{?dist}
License:            GPLv3+
Group:              System Tools
Source:             https://munge.googlecode.com/files/%{name}-%{version}.tar.bz2
# Recognize Amazon Linux as RedHat-ish in init.d script
Patch0:             munge-amznlinuxinitd.patch
# Create /var/log/munge upon startup if nonexistent
Patch1:             munge-create-var-dirs.patch
Patch2:             munge-create-key.patch
URL:                https://code.google.com/p/munge/
Requires(pre):      /bin/egrep /usr/sbin/useradd
Requires(post):     /sbin/chkconfig /sbin/service
Requires(post):     /sbin/service
Requires:           openssl
BuildRequires:      openssl-devel

%description
MUNGE (MUNGE Uid 'N' Gid Emporium) is an authentication service for
creating and validating credentials. It is designed to be highly
scalable for use in an HPC cluster environment. It allows a process to
authenticate the UID and GID of another local or remote process within
a group of hosts having common users and groups. These hosts form a
security realm that is defined by a shared cryptographic key. Clients
within this security realm can create and validate credentials without
the use of root privileges, reserved ports, or platform-specific
methods.

%pre
/bin/egrep '^munge:' /etc/passwd > /dev/null || /usr/sbin/useradd --comment "MUNGE Uid N Gid Emporium" --system --shell /sbin/nologin --home / munge

%post
/sbin/chkconfig --level 2345 munge on

%preun
/sbin/service munge stop || true

%prep
%setup -q -n %{name}-%{version}
%patch0 -p0
%patch1 -p0
%patch2 -p0

%build
%configure
sed -i 's|^hardcode_libdir_flag_spec=.*|hardcode_libdir_flag_spec=""|g' libtool
sed -i 's|^runpath_var=LD_RUN_PATH|runpath_var=DIE_RPATH_DIE|g' libtool
make %{?_smp_mflags}

%install
rm -rf %{buildroot}
%make_install

%files
%doc JARGON NEWS PLATFORMS TODO DISCLAIMER.LLNS README README.MULTILIB COPYING.LESSER DISCLAIMER.UC QUICKSTART INSTALL COPYING META AUTHORS HISTORY
%{_sysconfdir}/*
%{_bindir}/*
%{_sbindir}/*
/usr/lib/tmpfiles.d/*
%{_libdir}/*
%{_includedir}/*
%{_mandir}/man1/*
%{_mandir}/man3/*
%{_mandir}/man7/*
%{_mandir}/man8/*
