# 0env: Adopt the environment of a ZeroInstall feed

`0env` is a utility to run a program or shell in the context of a [ZeroInstall][] feed. It requires ZeroInstall itself.

For brevity, The examples in this README assume that you have set up a `0env` alias using:

	$ 0install add 0env http://gfxmonk.net/dist/0install/0env.xml

If not, you can always use the long-form invocation - replace all instances of `0env` below with `0install run http://gfxmonk.net/dist/0install/0env.xml`.

## Motivation:

Tools like [RVM][], [Bundler][] and [virtualenv][] take care of maintaining a non-global installation - some folder on disk that contains its own packages, and tools for managing that installation. These take the approach of a global, stateful package manager, and restrict it to a sandboxed version, usually for the use of a single application.

Some additionally provide a way to "get into" the context of this install - so you can run things as if your application's dependencies were globally installed. Depending on the tool, this environment may get into various states of disrepair due to its stateful nature. In `virtualenv`, for example, it's not uncommon to have to delete the environment and start over. This isn't _hard_, but it is _slow_ and _annoying_ - worse still, it promotes superstition about the state of your environment.

ZeroInstall takes a new approach. Dependencies are never globally installed, rather they are downloaded when they are first needed, and from then on used *directly* from their read-only cached location (by modifying `$PATH`, `$LD_LIBRARY_PATH`, `$PYTHONPATH`, `$RUBYLIB`, etc before running your application). Because of this, there's no single on-disk location you can "activate" to step into the environment your application will see.

`0env` provides a way to do exactly that, by essentially launching an interactive shell instead of your application. It provides some additional features like modifying your prompt and allowing additional bindings or dependencies to be included, but that's the basic idea.

# Basic usage:

There are a few typical ways to use `0env`:

### Local development of ZeroInstall-based projects

This is much like using a tool like [RVM][] or [virtualenv][], but without requiring any setup steps. Just pass `0env` the path to a local ZeroInstall feed file:

	$ 0env myproject.xml

This starts a new shell with all environment variables set as needed for `myproject` and all its ZeroInstall-based dependencies. Press `<Ctrl-d>` (or run `exit`) to return to your original, unchanged shell.

### Interactive use of published feeds:

For projects you aren't working on but just want to try out or use temporarily, you can use their ZeroInstall feed URI directly:

	$ 0env http://gfxmonk.net/dist/0install/mocktest.xml

This will start a shell in which you can use the [`mocktest`][mocktest] python module without having to install anything using e.g [pip][] or [setuptools][].

When you exit the shell, `mocktest` will no longer be available - the only side effect to your computer will be some files added to `~/.cache/0install.net` (and only if `mocktest` was not yet cached or was out of date). This means you can try out libraries without worrying about them affecting global state or other programs, and without having to remember to uninstall anything.

### Non-interactive use:

Just like the POSIX `env` utility (or [Bundler][]'s `exec` command), `0env` can be used to run a command within the specified environment, rather than an interactive shell. E.g:

	$ 0env http://gfxmonk.net/dist/0install/mocktest.xml python ./tests.py

or, for a local feed:

	$ 0env mocktest.xml python ./tests.py

Note that you will usually need to use '--' in order to prevent `0env` from interpreting command arguments:

**Incorrect:**

	$ 0env http://gfxmonk.net/dist/0install/mocktest.xml python -i

**Correct:**

	$ 0env http://gfxmonk.net/dist/0install/mocktest.xml -- python -i


# Advanced usage:

`0env` provides many options to control its behaviour. Some are summarized below:

### Additional environment export:

If you want to export an additional environment variable with the path to a feed's implementation, you can use:

	$ 0env http://gfxmonk.net/dist/0install/mocktest.xml --replace=MOCKTEST_ROOT

This will set `$MOCKTEST_ROOT` to the root implementation path of `mocktest`. The details of what is exported can be controlled using the related `--insert`, `--prepend` and `--append` options.

### Multiple feeds:

You can combine the exports of multiple feeds with the `--and` (`-a`) option:

	$ 0env http://gfxmonk.net/dist/0install/mocktest.xml -a http://gfxmonk.net/dist/0install/rednose.xml

Note that all options that apply to a single feed  (`--replace`, `--executable-in-path`, `--command`, etc) will apply to the *primary* feed, not to any feeds specified using the `-a` option.

### Using within the same shell:

Since `0env` by design requires no installation, it is difficult to provide shell builtins and the ability to directly source an environment without having to start a sub-shell. However, if you really need this functionality you can use the `--export` option to write to a file, e.g:

	$ 0env --export=./env.sh http://gfxmonk.net/dist/0install/mocktest.xml
	$ source env.sh

This will activate mocktest within the currently active shell. To reset all variables to how they were when you ran `0env`, run:

	$ source env.sh undo

Please note that scripts generated with `--export` should not be expected to work on a different machine or even on the same machine at a later date, as they contain a snapshot of all relevant environment variables at the time of generation. If you're using this mechanism, you should regenerate the scripts regularly.

**Note**: while `0env` works just fine on windows, `0env --export` currently only outputs shell syntax, so you won't be able to use it with cmd.exe (but it should work fine in a Cygwin shell).

# What's that smell?

It is typical of systems like [RVM][], [virtualenv][], etc to contain some deeply stinky (and fragile) shell script code in order to bend the shell to their will. Hacks littered with special cases for different kinds of shells, glued together in the hope that your setup is sufficiently "normal" for the illusion to work well enough.

I detest such code - not only because it is unportable, hard to write and hard to test, but because it's prone to breaking in horribly surprising ways (fun story: the first day I used `rvm`, it overrode my `cd` command with one that never terminated. That should *never* happen).

`0env` keeps this kind of code to a minimum: the only code that could reasonably fail in the face of esoteric setups is used to set `$PS1` (the prompt) for a subshell - everything else is plain old portable python (proof: it even works on Windows ;)). If you have an obscure setup that `0env` can't quite handle, you can run with `--noprompt` to avoid this code entirely. The code also doesn't run when `0env` is run in batch mode (i.e when you provide a command line after the feed URI).

If you're not using the default prompt functionality, `0env` will still set `$ZEROENV_NAME` in the child process, so you can integrate this with your environment however you please.

## So it works everywhere?

Almost. It's known not to work under MinGW on Windows. This cannot be reasonably blamed on `0env` itself, as even the humble `os.system("bash")` doesn't work under that environment due to the gymnastics it performs on environment variables. Workarounds include using `cygwin`, `cmd.exe` or something that isn't Windows.

# A note about native packages:

[ZeroInstall][] (and by extension, `0env`) lets you try out applications and libraries without having to install anything or affect global state. There is one exception: ZeroInstall supports [native packages](http://0install.net/distribution-integration.html), which allow ZeroInstall feeds to depend on system packages (this only works on Linux, since other platforms don't have a system package manager). If you're using a feed that depends on a system package, ZeroInstall will warn you about this and require you to put in your administrator password. Unlike pure ZeroInstall implementations, system packages do affect your system's global state just like if you installed them manually (with `apt-get`, `yum`, etc).

[ZeroInstall]:  http://0install.net/
[RVM]:          http://rvm.io/
[Bundler]:      http://gembundler.com/
[virtualenv]:   http://www.virtualenv.org/
[pip]:          http://pypi.python.org/pypi/pip
[setuptools]:   http://pypi.python.org/pypi/setuptools
[mocktest]:     http://gfxmonk.net/dist/doc/mocktest/doc/
