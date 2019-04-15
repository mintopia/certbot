"""Module executing integration tests against certbot core."""
from __future__ import print_function

import os
import subprocess
from os.path import join

import pytest
from certbot_integration_tests.certbot_tests import context as certbot_context
from certbot_integration_tests.certbot_tests.assertions import (
    assert_hook_execution, assert_saved_renew_hook, assert_cert_count_for_lineage,
    assert_world_permissions, assert_equals_group_owner, assert_equals_permissions,
)
from certbot_integration_tests.utils import misc


@pytest.fixture()
def context(request):
    # Fixture request is a built-in pytest fixture describing current test request.
    integration_test_context = certbot_context.IntegrationTestsContext(request)
    try:
        yield integration_test_context
    finally:
        integration_test_context.cleanup()


def test_basic_commands(context):
    """Test simple commands on Certbot CLI."""
    # TMPDIR env variable is set to workspace for the certbot subprocess.
    # So tempdir module will create any temporary files/dirs in workspace,
    # and its content can be tested to check correct certbot cleanup.
    initial_count_tmpfiles = len(os.listdir(context.workspace))

    context.certbot(['--help'])
    context.certbot(['--help', 'all'])
    context.certbot(['--version'])

    with pytest.raises(subprocess.CalledProcessError):
        context.certbot(['--csr'])

    new_count_tmpfiles = len(os.listdir(context.workspace))
    assert initial_count_tmpfiles == new_count_tmpfiles


def test_hook_dirs_creation(context):
    """Test all hooks directory are created during Certbot startup."""
    context.certbot(['register'])

    for hook_dir in misc.list_renewal_hooks_dirs(context.config_dir):
        assert os.path.isdir(hook_dir)


def test_registration_override(context):
    """Test correct register/unregister, and registration override."""
    context.certbot(['register'])
    context.certbot(['unregister'])
    context.certbot(['register', '--email', 'ex1@domain.org,ex2@domain.org'])

    # TODO: When `certbot register --update-registration` is fully deprecated,
    #  delete the two following deprecated uses
    context.certbot(['register', '--update-registration', '--email', 'ex1@domain.org'])
    context.certbot(['register', '--update-registration', '--email', 'ex1@domain.org,ex2@domain.org'])

    context.certbot(['update_account', '--email', 'example@domain.org'])
    context.certbot(['update_account', '--email', 'ex1@domain.org,ex2@domain.org'])


def test_prepare_plugins(context):
    """Test that plugins are correctly instantiated and displayed."""
    output = context.certbot(['plugins', '--init', '--prepare'])

    assert 'webroot' in output


def test_http_01(context):
    """Test the HTTP-01 challenge using standalone plugin."""
    # We start a server listening on the port for the
    # TLS-SNI challenge to prevent regressions in #3601.
    with misc.create_http_server(context.tls_alpn_01_port):
        certname = context.get_domain('le2')
        context.certbot([
            '--domains', certname, '--preferred-challenges', 'http-01', 'run',
            '--cert-name', certname,
            '--pre-hook', 'echo wtf.pre >> "{0}"'.format(context.hook_probe),
            '--post-hook', 'echo wtf.post >> "{0}"'.format(context.hook_probe),
            '--deploy-hook', 'echo deploy >> "{0}"'.format(context.hook_probe)
        ])

    assert_hook_execution(context.hook_probe, 'deploy')
    assert_saved_renew_hook(context.config_dir, certname)


def test_manual_http_auth(context):
    """Test the HTTP-01 challenge using manual plugin."""
    with misc.create_http_server(context.http_01_port) as webroot,\
            misc.manual_http_hooks(webroot, context.http_01_port) as scripts:

        certname = context.get_domain()
        context.certbot([
            'certonly', '-a', 'manual', '-d', certname,
            '--cert-name', certname,
            '--manual-auth-hook', scripts[0],
            '--manual-cleanup-hook', scripts[1],
            '--pre-hook', 'echo wtf.pre >> "{0}"'.format(context.hook_probe),
            '--post-hook', 'echo wtf.post >> "{0}"'.format(context.hook_probe),
            '--renew-hook', 'echo renew >> "{0}"'.format(context.hook_probe)
        ])

    with pytest.raises(AssertionError):
        assert_hook_execution(context.hook_probe, 'renew')
    assert_saved_renew_hook(context.config_dir, certname)


def test_manual_dns_auth(context):
    """Test the DNS-01 challenge using manual plugin."""
    certname = context.get_domain('dns')
    context.certbot([
        '-a', 'manual', '-d', certname, '--preferred-challenges', 'dns',
        'run', '--cert-name', certname,
        '--manual-auth-hook', context.manual_dns_auth_hook,
        '--manual-cleanup-hook', context.manual_dns_cleanup_hook,
        '--pre-hook', 'echo wtf.pre >> "{0}"'.format(context.hook_probe),
        '--post-hook', 'echo wtf.post >> "{0}"'.format(context.hook_probe),
        '--renew-hook', 'echo renew >> "{0}"'.format(context.hook_probe)
    ])

    with pytest.raises(AssertionError):
        assert_hook_execution(context.hook_probe, 'renew')
    assert_saved_renew_hook(context.config_dir, certname)


def test_certonly(context):
    """Test the certonly verb on certbot."""
    context.certbot(['certonly', '--cert-name', 'newname', '-d', context.get_domain('newname')])


def test_auth_and_install_with_csr(context):
    """Test certificate issuance and install using an existing CSR."""
    certname = context.get_domain('le3')
    key_path = join(context.workspace, 'key.pem')
    csr_path = join(context.workspace, 'csr.der')

    misc.generate_csr([certname], key_path, csr_path)

    cert_path = join(context.workspace, 'csr', 'cert.pem')
    chain_path = join(context.workspace, 'csr', 'chain.pem')

    context.certbot([
        'auth', '--csr', csr_path,
        '--cert-path', cert_path,
        '--chain-path', chain_path
    ])

    print(misc.read_certificate(cert_path))
    print(misc.read_certificate(chain_path))

    context.certbot([
        '--domains', certname, 'install',
        '--cert-path', cert_path,
        '--key-path', key_path
    ])

    context.certbot(['renew', '--cert-name', certname, '--authenticator', 'manual'])

    assert_cert_count_for_lineage(context.config_dir, certname, 2)


def test_renew_files_permissions(context):
    """Test proper certificate file permissions upon renewal"""
    certname = context.get_domain('renew')
    context.certbot(['-d', certname])

    assert_cert_count_for_lineage(context.config_dir, certname, 1)
    assert_world_permissions(
        join(context.config_dir, 'archive', certname, 'privkey1.pem'), 0)

    context.certbot(['renew'])

    assert_cert_count_for_lineage(context.config_dir, certname, 2)
    assert_world_permissions(
        join(context.config_dir, 'archive', certname, 'privkey2.pem'), 0)
    assert_equals_group_owner(
        join(context.config_dir, 'archive', certname, 'privkey1.pem'),
        join(context.config_dir, 'archive', certname, 'privkey2.pem'))
    assert_equals_permissions(
        join(context.config_dir, 'archive', certname, 'privkey1.pem'),
        join(context.config_dir, 'archive', certname, 'privkey2.pem'), 0o074)


def test_renew_with_hook_scripts(context):
    """Test certificate renewal with script hooks."""
    certname = context.get_domain('renew')
    context.certbot(['-d', certname])

    assert_cert_count_for_lineage(context.config_dir, certname, 1)

    misc.generate_test_file_hooks(context.config_dir, context.hook_probe)
    context.certbot(['renew'])

    assert_cert_count_for_lineage(context.config_dir, certname, 2)
    assert_hook_execution(context.hook_probe, 'deploy')


def test_renew_files_propagate_permissions(context):
    """Test proper certificate renewal with custom permissions propagated on private key."""
    certname = context.get_domain('renew')
    context.certbot(['-d', certname])

    assert_cert_count_for_lineage(context.config_dir, certname, 1)

    os.chmod(join(context.config_dir, 'archive', certname, 'privkey1.pem'), 0o444)
    context.certbot(['renew'])

    assert_cert_count_for_lineage(context.config_dir, certname, 2)
    assert_world_permissions(
        join(context.config_dir, 'archive', certname, 'privkey2.pem'), 4)
    assert_equals_permissions(
        join(context.config_dir, 'archive', certname, 'privkey1.pem'),
        join(context.config_dir, 'archive', certname, 'privkey2.pem'), 0o074)


def test_graceful_renew_it_is_not_time(context):
    """Test graceful renew is not done when it is not due time."""
    certname = context.get_domain('renew')
    context.certbot(['-d', certname])

    assert_cert_count_for_lineage(context.config_dir, certname, 1)

    context.certbot_no_force_renew([
        'renew', '--deploy-hook', 'echo deploy >> "{0}"'.format(context.hook_probe)])

    assert_cert_count_for_lineage(context.config_dir, certname, 1)
    with pytest.raises(AssertionError):
        assert_hook_execution(context.hook_probe, 'deploy')


def test_graceful_renew_it_is_time(context):
    """Test graceful renew is done when it is due time."""
    certname = context.get_domain('renew')
    context.certbot(['-d', certname])

    assert_cert_count_for_lineage(context.config_dir, certname, 1)

    with open(join(context.config_dir, 'renewal', '{0}.conf'.format(certname)), 'r') as file:
        lines = file.readlines()
    lines.insert(4, 'renew_before_expiry = 100 years{0}'.format(os.linesep))
    with open(join(context.config_dir, 'renewal', '{0}.conf'.format(certname)), 'w') as file:
        file.writelines(lines)

    context.certbot_no_force_renew([
        'renew', '--deploy-hook', 'echo deploy >> "{0}"'.format(context.hook_probe)])

    assert_cert_count_for_lineage(context.config_dir, certname, 2)
    assert_hook_execution(context.hook_probe, 'deploy')


def test_renew_with_changed_private_key_complexity(context):
    """Test proper renew with updated private key complexity."""
    certname = context.get_domain('renew')
    context.certbot(['-d', certname, '--rsa-key-size', '4096'])

    key1 = join(context.config_dir, 'archive', certname, 'privkey1.pem')
    assert os.stat(key1).st_size > 3000  # 4096 bits keys takes more than 3000 bytes
    assert_cert_count_for_lineage(context.config_dir, certname, 1)

    context.certbot(['renew'])
    
    assert_cert_count_for_lineage(context.config_dir, certname, 2)
    key2 = join(context.config_dir, 'archive', certname, 'privkey2.pem')
    assert os.stat(key2).st_size > 3000

    context.certbot(['renew', '--rsa-key-size', '2048'])

    assert_cert_count_for_lineage(context.config_dir, certname, 3)
    key3 = join(context.config_dir, 'archive', certname, 'privkey3.pem')
    assert os.stat(key3).st_size < 1800  # 2048 bits keys takes less than 1800 bytes


def test_renew_ignoring_directory_hooks(context):
    """Test hooks are ignored during renewal with relevant CLI flag."""
    certname = context.get_domain('renew')
    context.certbot(['-d', certname])

    assert_cert_count_for_lineage(context.config_dir, certname, 1)

    misc.generate_test_file_hooks(context.config_dir, context.hook_probe)
    context.certbot(['renew', '--no-directory-hooks'])

    assert_cert_count_for_lineage(context.config_dir, certname, 2)
    with pytest.raises(AssertionError):
        assert_hook_execution(context.hook_probe, 'deploy')


def test_renew_empty_hook_scripts(context):
    """Test proper renew with empty hook scripts."""
    certname = context.get_domain('renew')
    context.certbot(['-d', certname])

    assert_cert_count_for_lineage(context.config_dir, certname, 1)

    misc.generate_test_file_hooks(context.config_dir, context.hook_probe)
    for hook_dir in misc.list_renewal_hooks_dirs(context.config_dir):
        shutil.rmtree(hook_dir)
        os.makedirs(join(hook_dir, 'dir'))
        open(join(hook_dir, 'file'), 'w').close()
    context.certbot(['renew'])

    assert_cert_count_for_lineage(context.config_dir, certname, 2)


def test_renew_hook_override(context):
    """Test correct hook override on renew."""
    certname = context.get_domain('override')
    context.certbot([
        'certonly', '-d', certname,
        '--preferred-challenges', 'http-01',
        '--pre-hook', 'echo pre >> "{0}"'.format(context.hook_probe),
        '--post-hook', 'echo post >> "{0}"'.format(context.hook_probe),
        '--deploy-hook', 'echo deploy >> "{0}"'.format(context.hook_probe)
    ])

    assert_hook_execution(context.hook_probe, 'pre')
    assert_hook_execution(context.hook_probe, 'post')
    assert_hook_execution(context.hook_probe, 'deploy')

    # Now we override all previous hooks during next renew.
    open(context.hook_probe, 'w').close()
    context.certbot([
        'renew', '--cert-name', certname,
        '--pre-hook', 'echo pre-override >> "{0}"'.format(context.hook_probe),
        '--post-hook', 'echo post-override >> "{0}"'.format(context.hook_probe),
        '--deploy-hook', 'echo deploy-override >> "{0}"'.format(context.hook_probe)
    ])

    assert_hook_execution(context.hook_probe, 'pre-override')
    assert_hook_execution(context.hook_probe, 'post-override')
    assert_hook_execution(context.hook_probe, 'deploy-override')
    with pytest.raises(AssertionError):
        assert_hook_execution(context.hook_probe, 'pre')
    with pytest.raises(AssertionError):
        assert_hook_execution(context.hook_probe, 'post')
    with pytest.raises(AssertionError):
        assert_hook_execution(context.hook_probe, 'deploy')

    # Expect that this renew will reuse new hooks registered in the previous renew.
    open(context.hook_probe, 'w').close()
    context.certbot(['renew', '--cert-name', certname])

    assert_hook_execution(context.hook_probe, 'pre-override')
    assert_hook_execution(context.hook_probe, 'post-override')
    assert_hook_execution(context.hook_probe, 'deploy-override')