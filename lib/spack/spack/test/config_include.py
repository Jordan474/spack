# Copyright 2013-2020 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
import pytest
import os
import os.path

from six import StringIO

from llnl.util.filesystem import mkdirp

import spack.config
import spack.environment
from spack.cmd.env import _env_create

# import spack.config
# import spack.environment as ev
# from spack.main import SpackCommand

# everything here uses the mock_env_path
pytestmark = pytest.mark.usefixtures(
    'mutable_mock_env_path', 'config')

def create_tmp_config_scope(tmpdir, dirname, **sections):
    """Create dirname directory in tmpdir containing config sections yaml files"""
    scopedir = tmpdir.join(dirname)
    mkdirp(scopedir)
    for section, content in sections.items():
        with open(scopedir.join(section + '.yaml'), 'w') as f:
            f.write(content)
    return scopedir

def test_config_include_section(tmpdir):
    """Test include.yaml config including another config scope"""
    scopea = create_tmp_config_scope(
        tmpdir, 'scopea', include="""\
include:
  - ../scopeb
""")
    scopeb = create_tmp_config_scope(
        tmpdir, 'scopeb', packages="""\
packages:
  mpileaks:
    version: [2.2]
""")

    # Sanity check
    packages = spack.config.config.get_config('packages')
    assert 'mpileaks' not in packages

    configscope = spack.config.ConfigScope('scopea', scopea)
    spack.config.config.push_scope(configscope)

    packages = spack.config.config.get_config('packages')
    assert 'mpileaks' in packages

    spack.config.config.remove_scope(configscope.name)

    packages = spack.config.config.get_config('packages')
    assert 'mpileaks' not in packages

def test_config_include_variable(tmpdir):
    """Test env variable in included path"""
    scopea = create_tmp_config_scope(
        tmpdir, 'scopea', include="""\
include:
  - ${TEST_ENV_VAR}/scopeb
""")
    scopeb = create_tmp_config_scope(
        tmpdir, 'scopeb', packages="""\
packages:
  mpileaks:
    version: [2.2]
""")
    os.environ['TEST_ENV_VAR'] = str(tmpdir)

    # Sanity check
    packages = spack.config.config.get_config('packages')
    assert 'mpileaks' not in packages

    configscope = spack.config.ConfigScope('scopea', scopea)
    spack.config.config.push_scope(configscope)

    packages = spack.config.config.get_config('packages')
    assert 'mpileaks' in packages

    os.environ.pop('TEST_ENV_VAR')

def test_config_include_in_env_configscope(tmpdir):
    """Test a spack-env including a config scope"""
    included = create_tmp_config_scope(
        tmpdir, 'included-config', packages="""\
packages:
  mpileaks:
    version: [2.2]
""")

    test_config = """\
env:
  include:
  - %s
  specs:
  - mpileaks
""" % str(included)
    _env_create('test', StringIO(test_config))
    e = spack.environment.read('test')

    # Sanity check
    assert not any('included-config' in n for n in spack.config.config.scopes.keys())

    with e:
        assert any('included-config' in n for n in spack.config.config.scopes.keys())
        # assert any(x.satisfies('mpileaks@2.2') for x in e._get_environment_specs())
    assert not any('included-config' in n for n in spack.config.config.scopes.keys())

def test_config_include_in_env_singlefile():
    """Test a spack-env including a "single file" config scope"""
    test_config = """\
env:
  include:
  - ./included-config.yaml
  specs:
  - mpileaks
"""
    _env_create('test', StringIO(test_config))
    e = spack.environment.read('test')

    with open(os.path.join(e.path, 'included-config.yaml'), 'w') as f:
        f.write("""\
packages:
  mpileaks:
    version: [2.2]
""")

    # Sanity check
    assert not any('included-config' in n for n in spack.config.config.scopes.keys())

    with e:
        assert any('included-config' in n for n in spack.config.config.scopes.keys())
        # assert any(x.satisfies('mpileaks@2.2') for x in e._get_environment_specs())
    assert not any('included-config' in n for n in spack.config.config.scopes.keys())

def test_config_include_multiple_levels(tmpdir):
    """Test multiple levels of includes"""
    scopes = []
    for i in range(3):
        scope = create_tmp_config_scope(
            tmpdir, 'testscope-%d' % i, include="""\
include:
  - ../testscope-%d
""" % (i + 1))
        scopes.append(scope)
    i += 1
    last = create_tmp_config_scope(
        tmpdir, 'testscope-%d' % i, packages="""\
packages:
  mpileaks:
    version: [2.2]
""")

    # Sanity check
    packages = spack.config.config.get_config('packages')
    assert 'mpileaks' not in packages

    configscope = spack.config.ConfigScope('testscope-0', scopes[0])
    spack.config.config.push_scope(configscope)

    packages = spack.config.config.get_config('packages')
    assert 'mpileaks' in packages

    spack.config.config.remove_scope(configscope.name)

    # All "testscope-%d" must have been removed
    assert not any('testscope-' in n for n in spack.config.config.scopes.keys())

    packages = spack.config.config.get_config('packages')
    assert 'mpileaks' not in packages
