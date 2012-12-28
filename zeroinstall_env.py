#!/usr/bin/env python
from __future__ import print_function

from optparse import OptionParser, OptionGroup
import sys, os, subprocess
import contextlib
import logging

import itertools
import tempfile
import shutil
import shlex
import cgi

LOGGER = logging.getLogger(__name__)

def parse_args(argv=None):
	'''
	>>> # (test setup ...)
	>>> try:
	...     from StringIO import StringIO
	... except ImportError: #py3
	...     from io import BytesIO as StringIO
	>>> def _try(fn, *a, **k):
	...   err = None
	...   sys.stderr = StringIO()
	...   try:
	...     return fn(*a, **k)
	...   except (Exception, SystemExit) as e:
	...     err = e
	...   finally:
	...     output = sys.stderr.getvalue().strip()
	...     if output: print(output)
	...     if err: print(str(err) if isinstance(err, AssertionError) else repr(err))
	>>> # (actual tests ...)
	>>> parsed = _try(parse_args, ["http://gfxmonk.net/dist/0install/mocktest.xml", "--command=foo", "--", "bash", "-c", 'echo 1'])
	>>> parsed.feed_command
	'foo'
	>>> parsed.command
	['bash', '-c', 'echo 1']
	>>> _try(parse_args, ["--export=/tmp/not/a/file", "http://gfxmonk.net/dist/0install/mocktest.xml", "--", "echo", "unexpected success"])
	Don't specify a command when using --export
	>>> _try(parse_args, ["--", "http://echo"]).feed
	'http://echo'
	>>> _try(parse_args, ["--", "local/path"]).feed == os.path.abspath('local/path')
	True
	>>> _try(parse_args, ["--", "echo", "command", "arg1"]).command
	['command', 'arg1']
	>>> _try(parse_args, ["--"])
	Usage: 0env [OPTIONS] feed [command [arg ...]]
	<BLANKLINE>
	Error: too few arguments
	>>> _try(parse_args, ["-x", "--frob", "feed"]).additional_args
	['--frob']
	'''
	p = OptionParser(
		prog='0env',
		usage="""0env [OPTIONS] feed [command [arg ...]]""",
		description="%prog runs a shell in the context of the named ZeroInstall feed (either a URL or a local file). " +
			"If `command` is given, it will run that command with any supplied arguments rather than an interactive shell."
		)

	p.add_option('--quiet', '-q', action='store_false', dest='verbose', default=None, help='Suppress all non-error output.')
	p.add_option('--verbose', '-v', action='store_true', dest='verbose', help='Print more information.')
	p.add_option('--gui', '-g', action='store_true', help='Show 0install GUI.', default=False)
	p.add_option('--console', '-c', action='store_true', help='Suppress 0install GUI, even for updates or first-run.', default=False)
	p.add_option('--refresh', '-r', action='store_true', help='Refresh all interfaces.', default=False)
	p.add_option('-x', metavar='ARG', dest='additional_args', action='append', default=[], help='Add an arbitrary argument to pass through to `0install run` (may be specified).')

	p.add_option('--command', dest='feed_command', metavar='command', default=None, help='Add command-specific bindings')
	p.add_option('--and', '-a', help='Add bindings from an additional URI.', dest='additional_uris', metavar='URI', action='append', default=[])
	p.add_option('--export', help='Export a `sh`-compatible script to be sourced, rather than starting a subshell directly.', default=False)

	prompt_group = OptionGroup(p, title="Interactive-mode settings")
	prompt_group.add_option('--prompt', dest='prompt', help='Set modified PS1 format. Default: \'%default\'', default="({label}) {prompt}")
	prompt_group.add_option('--prompt-label', help='Set the prompt label. Default: derived from feed URIs', dest='env_name')
	prompt_group.add_option('--noprompt', dest='prompt', action='store_const', const=False, help='Don\'t modify shell prompt (PS1). This may avoid errors with obscure shell setups.')
	prompt_group.add_option('--shell', metavar='COMMAND', help='Use COMMAND instead of $SHELL')
	prompt_group.add_option('--shell-type', metavar='TYPE', choices=('bash','zsh'), help='Assume your shell is compatible with TYPE')
	p.add_option_group(prompt_group)

	binding_group = OptionGroup(p, title="Exporting additional environment bindings",
			description="The syntax for BINDING in each of the options below is \"[insert:]envvar\"\ne.g: --prepend=src/bin:PATH or --replace=PROJECT_ROOT\n" +
			"Note: All of options in this group apply ONLY to the primary feed given, not to any additional feeds supplied using the -a/--and option.")
	binding_group.add_option('--replace', action='append', default=[], metavar='BINDING', help='Add a `replace` mode binding')
	binding_group.add_option('--prepend', action='append', default=[], metavar='BINDING', help='Add a `prepend` mode binding.')
	binding_group.add_option('--append', action='append', default=[], metavar='BINDING', help='Add an `append` mode binding.')
	binding_group.add_option('--executable-in-path', metavar='PROG', help='Place an executable named PROG on $PATH which runs the command ("run", or whatever is given to `--command`)')
	p.add_option_group(binding_group)

	opts, args = p.parse_args(argv)

	assert len(args) > 0, p.get_usage() + "\nError: too few arguments"
	opts.feed = args[0]
	opts.command = args[1:]
	opts.feed = expand_relative_uri(opts.feed)
	opts.additional_uris = list(map(expand_relative_uri, opts.additional_uris))
	opts.uris = [opts.feed] + opts.additional_uris
	if opts.export:
		assert len(opts.command) == 0, "Don't specify a command when using --export"
	return opts

def main(args=None):
	'''
	Run the 0env cli program.
	'''
	opts = parse_args(args)
	verbose = opts.verbose
	if verbose is True:
		level = logging.DEBUG
	elif verbose is False:
		level = logging.ERROR
	else:
		level = logging.INFO
	
	logging.basicConfig(level=level, format="%(message)s")

	with tempfile.NamedTemporaryFile(prefix='0env-', suffix='-feed.xml', delete=False) as feed_file:
		feed_path = feed_file.name
		feed_file.write(generate_feed(opts))
		LOGGER.debug("Generated temporary feed file: %s", feed_path)

	try:
		if opts.export:
			do_export(opts, feed_path)
			return 0
		else:
			return run_subshell(opts, feed_path)
	finally:
		os.remove(feed_path)


def zi_run_cmd(opts, *args):
	cmd = ['0install','run']
	cmd += opts.additional_args
	cmd += args
	logging.debug("Running ZI command: %r" % (cmd,))
	return cmd

def do_export(opts, feed_path):
	import json
	# dump out changes to env in a way that can be sourced by the shell
	proc = subprocess.Popen(zi_run_cmd(opts, feed_path, DUMP_ENV_PY), stdout=subprocess.PIPE)
	stdout, _ = proc.communicate()
	assert proc.wait() == 0, "Failed to resolve environment"
	new_env = json.loads(stdout)
	new_env['ZEROENV_NAME'] = get_env_name(opts)
	with open(opts.export, 'w') as script:
		exports, undo_exports = generate_exports_and_undo(os.environ.copy(), new_env)
		if opts.prompt is not False:
			exports.append("if [ -z \"$_ZEROENV_ORIG_PS1\" ]; then export _ZEROENV_ORIG_PS1=\"$PS1\"; fi")
			exports.append(export_PS1_sh(opts.prompt))
			undo_exports.append("export PS1=\"$_ZEROENV_ORIG_PS1\"")
		print(
			EXPORT_TEMPLATE.format(
				uris=", ".join(opts.uris),
				deactivate="\n".join(undo_exports),
				activate="\n".join(exports)
			) , file=script)

def run_subshell(opts, feed_path):
	shell_cmd = shlex.split(opts.shell or os.environ.get('SHELL', 'bash'))
	shell = detect_shell(opts.shell_type, shell_cmd)

	def run_shell(cmd):
		os.environ['ZEROENV_NAME'] = get_env_name(opts)
		LOGGER.debug("Running command: %r" % (cmd,))
		proc = subprocess.Popen(zi_run_cmd(opts, feed_path, RUN_ARGV_PY) + cmd)
		return proc.wait()

	if len(opts.command) == 0:
		if not (shell is None or opts.prompt is False):
			with shell.prompt_context(shell_cmd, opts.prompt) as cmd:
				return run_shell(cmd)
		return run_shell(shell_cmd)
	else:
		return run_shell(opts.command)

def generate_feed(opts, template=None):
	'''
	>>> class Object(object): pass
	>>> # a feed with the lot:
	>>> opts = Object()
	>>> opts.feed = 'URI &1'
	>>> opts.additional_uris = ['uri &2','uri 3']
	>>> opts.replace = ['src:SOURCE']
	>>> opts.prepend = ['bin<:PATH']
	>>> opts.append = [':ENV']
	>>> opts.feed_command = "run2&"
	>>> opts.executable_in_path = '<foo>'
	>>> print(generate_feed(opts=opts, template='{requirements}'))
	<requires interface="URI &amp;1" command="run2&amp;">
	<executable-in-path name="&lt;foo&gt;"/>
	<environment insert="src" name="SOURCE" mode="replace">
	<environment insert="bin&lt;" name="PATH" mode="prepend">
	<environment insert="" name="ENV" mode="append"></requires>
	<requires interface="uri &amp;2">
	</requires>
	<requires interface="uri 3">
	</requires>
	>>> # a feed with nothing much:
	>>> opts = Object()
	>>> opts.feed = 'URI'
	>>> opts.additional_uris = []
	>>> opts.replace = []
	>>> opts.prepend = []
	>>> opts.append = []
	>>> opts.feed_command = None
	>>> opts.executable_in_path = None
	>>> print(generate_feed(opts=opts, template='{requirements}'))
	<requires interface="URI">
	</requires>
	'''
	if template is None: template = FEED_TEMPLATE
	def exports_elem(mode, val):
		insert, name = tuple(map(cgi.escape, parse_binding(val)))
		return '<environment insert="%s" name="%s" mode="%s">' % (insert, name, mode)

	def requires_elem(uri, opts=None):
		elem = '<requires interface="%s"' % (cgi.escape(uri),)
		if opts and opts.feed_command:
			elem += ' command="%s"' % (cgi.escape(opts.feed_command),)
		elem += '>\n'

		if opts and opts.executable_in_path:
			elem += '<executable-in-path name="%s"/>\n' % (cgi.escape(opts.executable_in_path),)

		if opts:
			exports = (
				[exports_elem('replace', b) for b in opts.replace] +
				[exports_elem('prepend', b) for b in opts.prepend] +
				[exports_elem('append', b) for b in opts.append]
			)
			elem += "\n".join(exports)
		elem += '</requires>'
		return elem

	# Note that all special options apply only to the first feed,
	# the rest are called with `opts=None` so that they just come out as plain <requires/>
	requirements = [requires_elem(opts.feed, opts)] + list(map(requires_elem, opts.additional_uris))
	requirements = "\n".join(requirements)
	feed_content = template.format(requirements=requirements)
	# LOGGER.debug("Generated feed content:\n%s", feed_content)
	return feed_content


def detect_shell(shell_type, shell_cmd):
	'''
	>>> detect_shell("bash", [])
	#<Shell: bash>

	>>> detect_shell("zsh", [])
	#<Shell: zsh>

	>>> print(detect_shell(None, []))
	None

	>>> detect_shell(None, ['/usr/bin/bash'])
	#<Shell: bash>

	>>> detect_shell(None, ['/usr/bin/sh'])
	#<Shell: bash>

	>>> detect_shell(None, ['/usr/bin/bash', '--arg'])
	#<Shell: bash>

	>>> detect_shell(None, ['/usr/bin/env', 'zsh'])
	#<Shell: zsh>

	>>> detect_shell(None, ['zsh'])
	#<Shell: zsh>

	>>> print(detect_shell(None, ['fish']))
	None

	'''
	if shell_type is not None:
		return getattr(Shell, shell_type.upper())
	end_parts = list(map(os.path.basename, shell_cmd))
	for shell in Shell._all:
		for name in shell.names:
			if name in end_parts:
				return shell
	LOGGER.debug("Couldn't detect your shell type.")
	return None

def parse_binding(b):
	'''
	Return a tuple of (insert, envname) from a binding of the format "[insert:]name"

	>>> parse_binding("src/bin:PATH")
	('src/bin', 'PATH')

	>>> parse_binding(":PATH")
	('', 'PATH')

	>>> parse_binding("PATH")
	('', 'PATH')

	>>> parse_binding("C:/foo/bar:PATH")
	('C:/foo/bar', 'PATH')
	'''
	if ":" in b:
		return tuple(b.rsplit(":", 1))
	else:
		return ("", b)

def expand_relative_uri(uri):
	if "://" in uri:
		return uri
	return os.path.abspath(uri)

def get_short_feed_name(s):
	'''
	>>> get_short_feed_name("foo.xml")
	'foo'

	>>> get_short_feed_name("/foo/bar/baz/")
	'baz'

	>>> get_short_feed_name("foo")
	'foo'
	'''
	s = s.rstrip('/')
	s = s.rsplit('/', 1)[-1]
	if s.lower().endswith(".xml"):
		s = s[:-4]
	return s

def get_env_name(opts):
	if opts.env_name is not None:
		return opts.env_name
	return ",".join(map(get_short_feed_name, opts.uris))

def shell_escape(s):
	r'''
	Escape a string for inclusion as a shell literal

	>>> def roundtrip(s):
	...    return subprocess.check_output(['sh', '-c', 'echo ' + shell_escape(s)]).decode('utf-8')
	>>> print(roundtrip("$foo"), end='')
	$foo
	>>> print(roundtrip("cat's and \"hat's\"!!!''"), end='')
	cat's and "hat's"!!!''
	'''

	return "'%s'" % (s.replace("'",r"'\''"),)


def generate_exports_and_undo(old_env, new_env):
	r'''
	>>> (exports, undo_exports) = generate_exports_and_undo(
	...    {"changed": "old_val", "removed": "removed"},
	...    {"changed": "new_val", "added":"add's", "_":"internal variable"}
	... )
	>>> print("\n".join(sorted(exports)))
	export added='add'\''s'
	export changed='new_val'
	unset removed
	>>> print("\n".join(sorted(undo_exports)))
	export changed='old_val'
	export removed='removed'
	unset added
	'''
	exports = []
	undo_exports = []
	def change(key, new_val):
		if new_val is None:
			return 'unset %s' % (key,)
		return 'export %s=%s' % (key, shell_escape(new_val))

	def export(key, current, new):
		if key == '_': return # zsh private envvar
		exports.append(change(key, new))
		undo_exports.append(change(key, current))
	with_env_changes(old_env, new_env, export)
	return exports, undo_exports

def with_env_changes(a, b, action):
	for k in set(list(a.keys()) + list(b.keys())):
		av = a.get(k, None)
		bv = b.get(k, None)
		if av != bv:
			action(k, av, bv)

# Note that we have to wait() for the child process. os.execvp() on windows makes 0env think the child process is complete instantly,
# which leaves two shells contending for the same input
RUN_ARGV_PY = "import os,sys,subprocess; sys.exit(subprocess.Popen(sys.argv[1:]).wait())"
DUMP_ENV_PY = "import json,os,sys; json.dump(os.environ.copy(), sys.stdout)"
FEED_TEMPLATE = '''<?xml version="1.0" ?>
<interface xmlns="http://zero-install.sourceforge.net/2004/injector/interface">
	<name>0env Container</name>
	<summary>(auto generated)</summary>
	<description>
	</description>
	<group>
		<command name="run">
			<runner interface="http://repo.roscidus.com/python/python">
				<arg>-c</arg>
			</runner>
		</command>
		{requirements}
		<implementation id="." version="1.0"></implementation>
	</group>
</interface>
'''

EXPORT_TEMPLATE = '''
# Activates {uris}
# NOTE: this script must be sourced to have any effect

if [ "$#" -eq "1" -a "$1" = "undo" ]; then
{deactivate}
else
	if [ "$#" -gt 0 ]; then
		echo "ERROR: Unknown options: $@" >&2
	else
{activate}
	fi
fi
'''

############################################################
# Shell-specific functionality
############################################################

class Shell(object):
	_all = []
	def __init__(self, names, prompt_context):
		self.names = names
		self.prompt_context = lambda *a: prompt_context(self, *a)
		Shell._all.append(self)
	
	def __repr__(self):
		return "#<Shell: %s>" % (self.names[0],)
	
def export_PS1_sh(fmt):
	prompt = fmt.format(label="$ZEROENV_NAME", prompt='\'"$PS1"\'')
	# (hopefully) cross-shell synax for updating PS1 iff it doesn't already contain ZEROENV_NAME
	return '''
if [ -n "$ZSH_VERSION" ]; then setopt PROMPT_SUBST; fi
case "$PS1" in
*ZEROENV_NAME*) true;;
*)              export PS1='%s';;
esac
''' % (prompt,)

@contextlib.contextmanager
def zsh_prompt(self, cmd, prompt_format):
	orig_dotdir = os.environ.get('ZDOTDIR', None)
	dotdir = orig_dotdir
	if dotdir is None:
		dotdir = os.path.expanduser('~')
	dotdir = os.path.abspath(dotdir)
	tempdir = tempfile.mkdtemp('0env')
	for f in os.listdir(dotdir):
		if not f.startswith(".z"): continue
		path = os.path.join(dotdir, f)
		if os.path.isfile(path):
			dest = os.path.join(tempdir, f)
			if f == '.zshrc':
				shutil.copy(path, dest)
			else:
				os.symlink(path, dest)
	with open(os.path.join(tempdir, '.zshrc'), 'a') as f:
		f.write(export_PS1_sh(prompt_format))
	os.environ['ZDOTDIR'] = tempdir
	try:
		yield cmd
	finally:
		shutil.rmtree(tempdir)
		if orig_dotdir is None:
			del os.environ['ZDOTDIR']
		else:
			os.environ['ZDOTDIR'] = orig_dotdir

@contextlib.contextmanager
def bash_prompt(self, cmd, prompt_format):
	dotdir = os.path.expanduser('~')
	dotdir = os.path.abspath(dotdir)
	tempdir = tempfile.mkdtemp('0env')
	path = os.path.join(dotdir, '.bashrc')
	dest = os.path.join(tempdir, '.bashrc')
	if os.path.isfile(path):
		shutil.copy(path, dest)
	with open(dest, 'a') as f:
		f.write(export_PS1_sh(prompt_format))

	non_option = lambda x: not x.startswith('-')
	new_cmd = list(itertools.takewhile(non_option, cmd)) + ['--rcfile', dest] + list(itertools.dropwhile(non_option, cmd))
	try:
		yield new_cmd
	finally:
		shutil.rmtree(tempdir)

Shell.ZSH = Shell(["zsh", "rzsh"], zsh_prompt)
Shell.BASH = Shell(["bash", "sh", "rbash"], bash_prompt)

if __name__ == '__main__':
	try:
		sys.exit(main())
	except AssertionError as e:
		print(e, file=sys.stderr)
		sys.exit(1)

