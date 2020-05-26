# Copyright (c) 2011 Tencent Inc.
# All rights reserved.
#
# Author: Huan Yu <huanyu@tencent.com>
#         Feng Chen <phongchen@tencent.com>
#         Yi Wang <yiwang@tencent.com>
#         Chong Peng <michaelpeng@tencent.com>
# Date:   October 20, 2011


"""
 This is the build rules genearator module which invokes all the builder
 objects to generate build rules.
"""

from __future__ import absolute_import

import os
import sys
import textwrap
import time

from blade import blade_util
from blade import config
from blade import console
from blade.blade_platform import CcFlagsManager


def _incs_list_to_string(incs):
    """ Convert incs list to string
    ['thirdparty', 'include'] -> -I thirdparty -I include
    """
    return ' '.join(['-I ' + path for path in incs])


def protoc_import_path_option(incs):
    return ' '.join(['-I=%s' % inc for inc in incs])


class ScriptHeaderGenerator(object):
    """Generate global declarations and definitions for build script.

    Specifically it may consist of global functions and variables,
    environment setup, predefined rules and builders, utilities
    for the underlying build system.
    """

    def __init__(self, options, build_dir, build_platform, build_environment, svn_roots):
        self.rules_buf = []
        self.options = options
        self.build_dir = build_dir
        self.cc = build_platform.get_cc()
        self.cc_version = build_platform.get_cc_version()
        self.python_inc = build_platform.get_python_include()
        self.cuda_inc = build_platform.get_cuda_include()
        self.build_environment = build_environment
        self.ccflags_manager = CcFlagsManager(options, build_dir, build_platform)
        self.svn_roots = svn_roots

        self.distcc_enabled = config.get_item('distcc_config', 'enabled')

    def _add_rule(self, rule):
        """Append one rule to buffer. """
        self.rules_buf.append('%s\n' % rule)

    def _append_prefix_to_building_var(
            self,
            prefix='',
            building_var='',
            condition=False):
        """A helper method: append prefix to building var if condition is True."""
        if condition:
            return '%s %s' % (prefix, building_var)
        else:
            return building_var


class NinjaScriptHeaderGenerator(ScriptHeaderGenerator):
    # pylint: disable=too-many-public-methods
    def __init__(self, options, build_dir, blade_path, build_platform, blade):
        ScriptHeaderGenerator.__init__(
            self, options, build_dir, build_platform,
            blade.build_environment, blade.svn_root_dirs)
        self.blade = blade
        self.blade_path = blade_path
        self.__all_rule_names = set()

    def get_all_rule_names(self):
        return list(self.__all_rule_names)

    def generate_rule(self, name, command, description=None,
                      depfile=None, generator=False, pool=None,
                      restat=False, rspfile=None,
                      rspfile_content=None, deps=None):
        self.__all_rule_names.add(name)
        self._add_rule('rule %s' % name)
        self._add_rule('  command = %s' % command)
        if description:
            self._add_rule('  description = %s' % console.colored(description, 'dimpurple'))
        if depfile:
            self._add_rule('  depfile = %s' % depfile)
        if generator:
            self._add_rule('  generator = 1')
        if pool:
            self._add_rule('  pool = %s' % pool)
        if restat:
            self._add_rule('  restat = 1')
        if rspfile:
            self._add_rule('  rspfile = %s' % rspfile)
        if rspfile_content:
            self._add_rule('  rspfile_content = %s' % rspfile_content)
        if deps:
            self._add_rule('  deps = %s' % deps)

    def generate_file_header(self):
        self._add_rule(textwrap.dedent('''\
                # build.ninja generated by blade
                ninja_required_version = 1.7
                builddir = %s''') % self.build_dir)
        # No more than 1 heavy target at a time
        self._add_rule(textwrap.dedent('''\
                pool heavy_pool
                  depth = 1
                '''))

    def generate_common_rules(self):
        self.generate_rule(name='stamp',
                           command='touch ${out}',
                           description='STAMP ${out}')
        self.generate_rule(name='copy',
                           command='cp -f ${in} ${out}',
                           description='COPY ${in} ${out}')

    def generate_cc_warning_vars(self):
        warnings, cxx_warnings, c_warnings = self.ccflags_manager.get_warning_flags()
        c_warnings += warnings
        cxx_warnings += warnings
        self._add_rule(textwrap.dedent('''\
                c_warnings = %s
                cxx_warnings = %s
                ''') % (' '.join(c_warnings), ' '.join(cxx_warnings)))

    def generate_cc_rules(self):
        # pylint: disable=too-many-locals
        build_with_ccache = self.build_environment.ccache_installed
        cc = os.environ.get('CC', 'gcc')
        cxx = os.environ.get('CXX', 'g++')
        ld = os.environ.get('LD', 'g++')
        if build_with_ccache:
            os.environ['CCACHE_BASEDIR'] = self.build_environment.blade_root_dir
            os.environ['CCACHE_NOHASHDIR'] = 'true'
            cc = 'ccache ' + cc
            cxx = 'ccache ' + cxx
        cc_config = config.get_section('cc_config')
        cc_library_config = config.get_section('cc_library_config')
        cflags, cxxflags = cc_config['cflags'], cc_config['cxxflags']
        cppflags, ldflags = self.ccflags_manager.get_flags_except_warning()
        cppflags = cc_config['cppflags'] + cppflags
        arflags = ''.join(cc_library_config['arflags'])
        ldflags = cc_config['linkflags'] + ldflags
        includes = cc_config['extra_incs']
        includes = includes + ['.', self.build_dir]
        includes = ' '.join(['-I%s' % inc for inc in includes])

        self.generate_cc_warning_vars()
        self.generate_rule(name='cc',
                           command='%s -o ${out} -MMD -MF ${out}.d '
                                   '-c -fPIC %s %s ${c_warnings} ${cppflags} '
                                   '%s ${includes} ${in}' % (
                                       cc, ' '.join(cflags), ' '.join(cppflags), includes),
                           description='CC ${in}',
                           depfile='${out}.d',
                           deps='gcc')
        self.generate_rule(name='cxx',
                           command='%s -o ${out} -MMD -MF ${out}.d '
                                   '-c -fPIC %s %s ${cxx_warnings} ${cppflags} '
                                   '%s ${includes} ${in}' % (
                                       cxx, ' '.join(cxxflags), ' '.join(cppflags), includes),
                           description='CXX ${in}',
                           depfile='${out}.d',
                           deps='gcc')
        if config.get_item('cc_config', 'header_inclusion_dependencies'):
            preprocess = '%s -o /dev/null -E -H %s %s -w ${cppflags} %s ${includes} ${in} 2>${out}'
            self.generate_rule(name='cchdrs',
                               command=preprocess % (cc, ' '.join(cflags), ' '.join(cppflags), includes),
                               description='CC HDRS ${in}')
            self.generate_rule(name='cxxhdrs',
                               command=preprocess % (cxx, ' '.join(cxxflags), ' '.join(cppflags), includes),
                               description='CXX HDRS ${in}')
        securecc = '%s %s' % (cc_config['securecc'], cxx)
        self._add_rule(textwrap.dedent('''\
                build __securecc_phony__ : phony
                '''))
        self.generate_rule(name='securecccompile',
                           command='%s -o ${out} -c -fPIC '
                                   '%s %s ${cxx_warnings} ${cppflags} %s ${includes} ${in}' % (
                                       securecc, ' '.join(cxxflags), ' '.join(cppflags), includes),
                           description='SECURECC ${in}')
        self.generate_rule(name='securecc',
                           command=self._toolchain_command('securecc_object'),
                           description='SECURECC ${in}',
                           restat=True)

        self.generate_rule(name='ar',
                           command='rm -f $out; ar %s $out $in' % arflags,
                           description='AR ${out}')
        link_jobs = config.get_item('link_config', 'link_jobs')
        if link_jobs:
            link_jobs = min(link_jobs, self.blade.parallel_jobs_num())
            console.info('tunes the parallel link jobs to be %s' % link_jobs)
            pool = 'link_pool'
            self._add_rule(textwrap.dedent('''\
                    pool %s
                      depth = %s''') % (pool, link_jobs))
        else:
            pool = None
        self.generate_rule(name='link',
                           command='%s -o ${out} %s ${ldflags} ${in} ${extra_ldflags}' % (
                               ld, ' '.join(ldflags)),
                           description='LINK ${out}',
                           pool=pool)
        self.generate_rule(name='solink',
                           command='%s -o ${out} -shared %s ${ldflags} ${in} ${extra_ldflags}' % (
                               ld, ' '.join(ldflags)),
                           description='SHAREDLINK ${out}',
                           pool=pool)
        self.generate_rule(name='strip',
                           command='strip --strip-unneeded -o ${out} ${in}',
                           description='STRIP ${out}')

    def generate_proto_rules(self):
        proto_config = config.get_section('proto_library_config')
        protoc = proto_config['protoc']
        protoc_java = protoc
        if proto_config['protoc_java']:
            protoc_java = proto_config['protoc_java']
        protobuf_incs = protoc_import_path_option(proto_config['protobuf_incs'])
        protobuf_java_incs = protobuf_incs
        if proto_config['protobuf_java_incs']:
            protobuf_java_incs = protoc_import_path_option(proto_config['protobuf_java_incs'])
        self._add_rule(textwrap.dedent('''\
                protocflags =
                protoccpppluginflags =
                protocjavapluginflags =
                protocpythonpluginflags =
                '''))
        self.generate_rule(name='proto',
                           command='%s --proto_path=. %s -I=`dirname ${in}` '
                                   '--cpp_out=%s ${protocflags} ${protoccpppluginflags} ${in}' % (
                                       protoc, protobuf_incs, self.build_dir),
                           description='PROTOC ${in}')
        self.generate_rule(name='protojava',
                           command='%s --proto_path=. %s --java_out=%s/`dirname ${in}` '
                                   '${protocjavapluginflags} ${in}' % (
                                       protoc_java, protobuf_java_incs, self.build_dir),
                           description='PROTOCJAVA ${in}')
        self.generate_rule(name='protopython',
                           command='%s --proto_path=. %s -I=`dirname ${in}` '
                                   '--python_out=%s ${protocpythonpluginflags} ${in}' % (
                                       protoc, protobuf_incs, self.build_dir),
                           description='PROTOCPYTHON ${in}')
        self.generate_rule(name='protodescriptors',
                           command='%s --proto_path=. %s -I=`dirname ${first}` '
                                   '--descriptor_set_out=${out} --include_imports '
                                   '--include_source_info ${in}' % (
                                       protoc, protobuf_incs),
                           description='PROTODESCRIPTORS ${in}')
        protoc_go_plugin = proto_config['protoc_go_plugin']
        if protoc_go_plugin:
            go_home = config.get_item('go_config', 'go_home')
            go_module_enabled = config.get_item('go_config', 'go_module_enabled')
            go_module_relpath = config.get_item('go_config', 'go_module_relpath')
            if not go_home:
                console.error_exit('go_home is not configured in either BLADE_ROOT or BLADE_ROOT.local.')
            if go_module_enabled and not go_module_relpath:
                outdir = proto_config['protobuf_go_path']
            else:
                outdir = os.path.join(go_home, 'src')
            subplugins = proto_config['protoc_go_subplugins']
            if subplugins:
                go_out = 'plugins=%s:%s' % ('+'.join(subplugins), outdir)
            else:
                go_out = outdir
            self.generate_rule(name='protogo',
                               command='%s --proto_path=. %s -I=`dirname ${in}` '
                                       '--plugin=protoc-gen-go=%s --go_out=%s ${in}' % (
                                           protoc, protobuf_incs, protoc_go_plugin, go_out),
                               description='PROTOCGOLANG ${in}')

    def generate_resource_rules(self):
        args = '${name} ${path} ${out} ${in}'
        self.generate_rule(name='resource_index',
                           command=self._toolchain_command('resource_index', suffix=args),
                           description='RESOURCE INDEX ${out}')
        self.generate_rule(name='resource',
                           command='xxd -i ${in} | '
                                   'sed -e "s/^unsigned char /const char RESOURCE_/g" '
                                   '-e "s/^unsigned int /const unsigned int RESOURCE_/g" > ${out}',
                           description='RESOURCE ${in}')

    def get_java_command(self, java_config, cmd):
        java_home = java_config['java_home']
        if java_home:
            return os.path.join(java_home, 'bin', cmd)
        return cmd

    def generate_javac_rules(self, java_config):
        javac = self.get_java_command(java_config, 'javac')
        jar = self.get_java_command(java_config, 'jar')
        cmd = [javac]
        version = java_config['version']
        source_version = java_config.get('source_version', version)
        target_version = java_config.get('target_version', version)
        if source_version:
            cmd.append('-source %s' % source_version)
        if target_version:
            cmd.append('-target %s' % target_version)
        cmd += [
            '-encoding ${source_encoding}',
            '-d ${classes_dir}',
            '-classpath ${classpath}',
            '${javacflags}',
            '${in}',
        ]
        self._add_rule(textwrap.dedent('''\
                source_encoding = UTF-8
                classpath = .
                javacflags =
                '''))
        self.generate_rule(name='javac',
                           command='rm -fr ${classes_dir} && mkdir -p ${classes_dir} && '
                                   '%s && sleep 0.5 && '
                                   '%s cf ${out} -C ${classes_dir} .' % (
                                       ' '.join(cmd), jar),
                           description='JAVAC ${in}')

    def generate_java_resource_rules(self):
        self.generate_rule(name='javaresource',
                           command=self._toolchain_command('java_resource'),
                           description='JAVA RESOURCE ${in}')

    def generate_java_test_rules(self):
        jacoco_home = config.get_item('java_test_config', 'jacoco_home')
        if jacoco_home:
            jacoco_agent = os.path.join(jacoco_home, 'lib', 'jacocoagent.jar')
            prefix = 'BLADE_JACOCOAGENT=%s' % jacoco_agent
        else:
            prefix = ''
        self._add_rule('javatargetundertestpkg = __targetundertestpkg__')
        args = '${mainclass} ${javatargetundertestpkg} ${out} ${in}'
        self.generate_rule(name='javatest',
                           command=self._toolchain_command('java_test', prefix=prefix, suffix=args),
                           description='JAVA TEST ${out}')

    def generate_java_binary_rules(self):
        bootjar = config.get_item('java_binary_config', 'one_jar_boot_jar')
        args = '%s ${mainclass} ${out} ${in}' % bootjar
        self.generate_rule(name='onejar',
                           command=self._toolchain_command('java_onejar', suffix=args),
                           description='ONE JAR ${out}')
        self.generate_rule(name='javabinary',
                           command=self._toolchain_command('java_binary'),
                           description='JAVA BIN ${out}')

    def generate_scala_rules(self, java_config):
        scala_home = config.get_item('scala_config', 'scala_home')
        if scala_home:
            scala = os.path.join(scala_home, 'bin', 'scala')
            scalac = os.path.join(scala_home, 'bin', 'scalac')
        else:
            scala = 'scala'
            scalac = 'scalac'
        java = self.get_java_command(java_config, 'java')
        self._add_rule(textwrap.dedent('''\
                scalacflags = -nowarn
                '''))
        cmd = [
            'JAVACMD=%s' % java,
            scalac,
            '-encoding UTF8',
            '-d ${out}',
            '-classpath ${classpath}',
            '${scalacflags}',
            '${in}'
        ]
        self.generate_rule(name='scalac',
                           command=' '.join(cmd),
                           description='SCALAC ${out}')
        args = '%s %s ${out} ${in}' % (java, scala)
        self.generate_rule(name='scalatest',
                           command=self._toolchain_command('scala_test', suffix=args),
                           description='SCALA TEST ${out}')

    def generate_java_scala_rules(self):
        java_config = config.get_section('java_config')
        self.generate_javac_rules(java_config)
        self.generate_java_resource_rules()
        jar = self.get_java_command(java_config, 'jar')
        args = '%s ${out} ${in}' % jar
        self.generate_rule(name='javajar',
                           command=self._toolchain_command('java_jar', suffix=args),
                           description='JAVA JAR ${out}')
        self.generate_java_test_rules()
        self.generate_rule(name='fatjar',
                           command=self._toolchain_command('java_fatjar'),
                           description='FAT JAR ${out}')
        self.generate_java_binary_rules()
        self.generate_scala_rules(java_config)

    def generate_thrift_rules(self):
        thrift_config = config.get_section('thrift_config')
        incs = _incs_list_to_string(thrift_config['thrift_incs'])
        gen_params = thrift_config['thrift_gen_params']
        thrift = thrift_config['thrift']
        if thrift.startswith('//'):
            thrift = thrift.replace('//', self.build_dir + '/')
            thrift = thrift.replace(':', '/')
        self.generate_rule(name='thrift',
                           command='%s --gen %s '
                                   '-I . %s -I `dirname ${in}` '
                                   '-out %s/`dirname ${in}` ${in}' % (
                                       thrift, gen_params, incs, self.build_dir),
                           description='THRIFT ${in}')

    def generate_python_rules(self):
        self._add_rule(textwrap.dedent('''\
                pythonbasedir = __pythonbasedir__
                '''))
        args = '${pythonbasedir} ${out} ${in}'
        self.generate_rule(name='pythonlibrary',
                           command=self._toolchain_command('python_library', suffix=args),
                           description='PYTHON LIBRARY ${out}')
        args = '${pythonbasedir} ${mainentry} ${out} ${in}'
        self.generate_rule(name='pythonbinary',
                           command=self._toolchain_command('python_binary', suffix=args),
                           description='PYTHON BINARY ${out}')

    def generate_go_rules(self):
        go_home = config.get_item('go_config', 'go_home')
        go = config.get_item('go_config', 'go')
        go_module_enabled = config.get_item('go_config', 'go_module_enabled')
        go_module_relpath = config.get_item('go_config', 'go_module_relpath')
        if go_home and go:
            go_pool = 'golang_pool'
            self._add_rule(textwrap.dedent('''\
                    pool %s
                      depth = 1''') % go_pool)
            go_path = os.path.normpath(os.path.abspath(go_home))
            out_relative = ""
            if go_module_enabled:
                prefix = go
                if go_module_relpath:
                    relative_prefix = os.path.relpath(prefix, go_module_relpath)
                    prefix = "cd {go_module_relpath} && {relative_prefix}".format(
                        go_module_relpath=go_module_relpath,
                        relative_prefix=relative_prefix,
                    )
                    # add slash to the end of the relpath
                    out_relative = os.path.join(os.path.relpath("./", go_module_relpath), "")
            else:
                prefix = 'GOPATH=%s %s' % (go_path, go)
            self.generate_rule(name='gopackage',
                               command='%s install ${extra_goflags} ${package}' % prefix,
                               description='GOLANG PACKAGE ${package}',
                               pool=go_pool)
            self.generate_rule(name='gocommand',
                               command='%s build -o %s${out} ${extra_goflags} ${package}' % (prefix, out_relative),
                               description='GOLANG COMMAND ${package}',
                               pool=go_pool)
            self.generate_rule(name='gotest',
                               command='%s test -c -o %s${out} ${extra_goflags} ${package}' % (prefix, out_relative),
                               description='GOLANG TEST ${package}',
                               pool=go_pool)

    def generate_shell_rules(self):
        self.generate_rule(name='shelltest',
                           command=self._toolchain_command('shell_test'),
                           description='SHELL TEST ${out}')
        args = '${out} ${in} ${testdata}'
        self.generate_rule(name='shelltestdata',
                           command=self._toolchain_command('shell_testdata', suffix=args),
                           description='SHELL TEST DATA ${out}')

    def generate_lex_yacc_rules(self):
        self.generate_rule(name='lex',
                           command='flex ${lexflags} -o ${out} ${in}',
                           description='LEX ${in}')
        self.generate_rule(name='yacc',
                           command='bison ${yaccflags} -o ${out} ${in}',
                           description='YACC ${in}')

    def generate_package_rules(self):
        args = '${out} ${in} ${entries}'
        self.generate_rule(name='package',
                           command=self._toolchain_command('package', suffix=args),
                           description='PACKAGE ${out}')
        self.generate_rule(name='package_tar',
                           command='tar -c -f ${out} ${tarflags} -C ${packageroot} ${entries}',
                           description='TAR ${out}')
        self.generate_rule(name='package_zip',
                           command='cd ${packageroot} && zip -q temp_archive.zip ${entries} && '
                                   'cd - && mv ${packageroot}/temp_archive.zip ${out}',
                           description='ZIP ${out}')

    def generate_version_rules(self):
        revision, url = blade_util.load_scm(self.build_dir)
        args = '${out} ${revision} ${url} ${profile} "${compiler}"'
        self.generate_rule(name='scm',
                           command=self._toolchain_command('scm', suffix=args),
                           description='SCM ${out}')
        scm = os.path.join(self.build_dir, 'scm.cc')
        self._add_rule(textwrap.dedent('''\
                build %s: scm
                  revision = %s
                  url = %s
                  profile = %s
                  compiler = %s
                ''') % (scm, revision, url, self.options.profile, '%s %s' % (self.cc, self.cc_version)))
        self._add_rule(textwrap.dedent('''\
                build %s: cxx %s
                  cppflags = -w -O2
                  cxx_warnings =
                ''') % (scm + '.o', scm))

    def _toolchain_command(self, builder, prefix='', suffix=''):
        cmd = ['PYTHONPATH=%s:$$PYTHONPATH' % self.blade_path]
        if prefix:
            cmd.append(prefix)
        cmd.append('%s -m blade.toolchain %s' % (sys.executable, builder))
        if suffix:
            cmd.append(suffix)
        else:
            cmd.append('${out} ${in}')
        return ' '.join(cmd)

    def generate(self):
        """Generate ninja rules. """
        self.generate_file_header()
        self.generate_common_rules()
        self.generate_cc_rules()
        self.generate_proto_rules()
        self.generate_resource_rules()
        self.generate_java_scala_rules()
        self.generate_thrift_rules()
        self.generate_python_rules()
        self.generate_go_rules()
        self.generate_shell_rules()
        self.generate_lex_yacc_rules()
        self.generate_package_rules()
        self.generate_version_rules()
        return self.rules_buf


class RulesGenerator(object):
    """
    Generate build rules according to underlying build system and blade options.
    This class should be inherited by particular build system generator.
    """

    def __init__(self, script_path, blade_path, blade):
        self.script_path = script_path
        self.blade_path = blade_path
        self.blade = blade
        self.build_platform = self.blade.get_build_platform()
        self.build_dir = self.blade.get_build_path()

    def get_all_rule_names(self):
        """Get all build rule names"""
        return []

    def generate_build_rules(self):
        """Generate build rules for underlying build system. """
        raise NotImplementedError

    def generate_build_script(self):
        """Generate build script for underlying build system. """
        rules = self.generate_build_rules()
        script = open(self.script_path, 'w')
        script.writelines(rules)
        script.close()
        return rules


class NinjaRulesGenerator(RulesGenerator):
    """Generate ninja rules to build.ninja. """

    def __init__(self, ninja_path, blade_path, blade):
        RulesGenerator.__init__(self, ninja_path, blade_path, blade)
        self.__all_rule_names = []

    def get_all_rule_names(self):  # override
        return self.__all_rule_names

    def generate_build_rules(self):
        """Generate ninja rules to build.ninja. """
        ninja_script_header_generator = NinjaScriptHeaderGenerator(
            self.blade.get_options(),
            self.build_dir,
            self.blade_path,
            self.build_platform,
            self.blade)
        rules = ninja_script_header_generator.generate()
        rules += self.blade.gen_targets_rules()
        self.__all_rule_names = ninja_script_header_generator.get_all_rule_names()
        return rules
