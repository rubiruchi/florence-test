#!/usr/bin/env python
"""
@package florence
SDN security test framework top level script
"""
import sys
import argparse
import logging
import unittest
import os
import imp
import fnmatch
from florence import config, DEBUG_LEVELS, CONFIG_DEFAULT


def florence_arg_setup():
    """
    Set up the configuration including parsing the arguments
    @return A pair (config, args) where config is an config
    object and args is any additional arguments from the command line
    """
    usage = "%(prog)s [options] (test|group)..."
    description = """Security framework to validate SDN switch & controller"""

    # Parse --interface
    def check_interface(option, opt, value):
        try:
            ofport, interface = value.split('@', 1)
            ofport = int(ofport)
        except ValueError:
            raise argparse.ArgumentError("incorrect interface syntax (got"
                                         "%s, expected 'ofport@interface')"
                                         % repr(value))
        return (ofport, interface)

    parser = argparse.ArgumentParser(usage=usage, description=description)

    # Set up default values
    parser.set_defaults(**CONFIG_DEFAULT)
    parser.add_argument('-V', '--version', action='version',
                        version='%(prog)s 0.1')
    parser.add_argument("--list", action="store_true",
                        help="List all tests and exit")
    parser.add_argument("--list-test-names", action='store_true',
                        help="List test names matching the test spec and exit")

    # Test options
    group = parser.add_argument_group("Test selection options")
    group.add_argument("--test-dir", help="Directory containing tests")

    # Switch and Controller options
    group = parser.add_argument_group("Switch connection options")
    group.add_argument("-H", "--host", dest="controller_host",
                       help="IP address to listen on (default %%default)")
    help_text = "Port number to listen on (default %%default)"
    group.add_argument("-p", "--port", dest="controller_port",
                       type=int, help=help_text)
    group.add_argument("-S", "--switch-ip", dest="switch_ip",
                       help="If set, actively connect to this switch by IP")
    help_text = "Specify one (or more) OpenFlow port and" \
                "the dataplane interface Example: 1@eth1"
    group.add_argument("--interface", "-i", type=check_interface,
                       metavar="INTERFACE", action="append",
                       help=help_text)

    # Logging options
    group = parser.add_argument_group("Logging options")
    group.add_argument("--log-file", help="Log file name (default %%default)")
    group.add_argument("--log-dir", help="Name of log directory")
    dbg_lvl_names = sorted(DEBUG_LEVELS.keys(), key=lambda x: DEBUG_LEVELS[x])
    help_text = "debug, info, warning, error, critical (default %%default)"
    group.add_argument("--debug", choices=dbg_lvl_names, help=help_text)
    group.add_argument("-v", "--verbose", action="store_const", dest="debug",
                       const="verbose", help="Shortcut for --debug=verbose")
    group.add_argument("-q", "--quiet", action="store_const", dest="debug",
                       const="warning", help="Shortcut for --debug=warning")

    # xunit options
    help_text = "Enable xUnit-formatted results"
    group.add_argument("--xunit", action="store_true", help=help_text)
    help_text = "Output directory for xUnit-formatted results"
    group.add_argument("--xunit-dir", help=help_text)

    # Optional test specific options
    group = parser.add_argument_group("Test behavior options")
    group.add_argument("--relax", action="store_true",
                       help="Relax packet match checks allowing other packets")
    test_params_help = """Set test parameters: key=val;... (see --list)
    """
    group.add_argument("-t", "--test-params", help=test_params_help)
    group.add_argument("--fail-skipped", action="store_true",
                       help="Return failure if any test was skipped")
    group.add_argument("--default-timeout", type=float,
                       help="Timeout in seconds for most operations")
    group.add_argument("--minsize", type=int,
                       help="Minimum allowable packet size on the dataplane.")
    group.add_argument("--random-seed", type=int,
                       help="Random number generator seed")
    group.add_argument("--disable-ipv6", action="store_true",
                       help="Disable IPv6 tests")
    group.add_argument("--random-order", action="store_true",
                       help="Randomize order of tests")

    # Process positional arguments
    parser.add_argument('posargs', nargs='*')

    args = parser.parse_args()

    # TODO If --test-dir wasn't given, pick one based on the OpenFlow version
    # Currently florence supports OpenFlow 1.3
    # if args.test_dir is None:
    #    args.test_dir = os.path.join(ROOT_DIR, "test")

    # Convert options from a Namespace to a plain dictionary
    config = CONFIG_DEFAULT.copy()
    for key in config.keys():
        config[key] = getattr(args, key)

    return (config, args.posargs)


def logging_setup():
    """
    Set up logging based on config
    """

    logging.getLogger().setLevel(DEBUG_LEVELS[config["debug"]])

    if config["log_dir"] is not None:
        if os.path.exists(config["log_dir"]):
            import shutil
            shutil.rmtree(config["log_dir"])
        os.makedirs(config["log_dir"])
    else:
        if os.path.exists(config["log_file"]):
            os.remove(config["log_file"])

    open_logfile('main')


def xunit_setup():
    """
    Set up xUnit output based on config
    """

    if not config["xunit"]:
        return

    if os.path.exists(config["xunit_dir"]):
        import shutil
        shutil.rmtree(config["xunit_dir"])
    os.makedirs(config["xunit_dir"])


def load_test_modules():
    """
    Load tests from the test directory.
    Also updates the _groups member to include "standard" and
    module test groups if appropriate.
    @param config The oft configuration dictionary
    @returns A dictionary from test module names to tuples of
    (module, dictionary from test names to test classes).
    """

    result = {}

    for root, dirs, filenames in os.walk(config["test_dir"]):
        # Iterate over each python file
        for filename in fnmatch.filter(filenames, '[!.]*.py'):
            modname = os.path.splitext(os.path.basename(filename))[0]

            try:
                if sys.modules.has_key(modname):
                    mod = sys.modules[modname]
                else:
                    mod = imp.load_module(modname, *imp.find_module(modname,
                                                                    [root]))
            except:
                logging.warning("Could not import file " + filename)
                raise

            # Find all testcases defined in the module
            tests = (dict((k, v) for (k, v) in mod.__dict__.items()
                     if type(v) == type and
                     issubclass(v, unittest.TestCase) and
                     hasattr(v, "runTest")))
            if tests:
                for (testname, test) in tests.items():
                    # Set default annotation values
                    if not hasattr(test, "_groups"):
                        test._groups = []
                    if not hasattr(test, "_nonstandard"):
                        test._nonstandard = False
                    if not hasattr(test, "_disabled"):
                        test._disabled = False

                    # Put test in its module's test group
                    if not test._disabled:
                        test._groups.append(modname)

                    # Put test in the standard test group
                    if not test._disabled and not test._nonstandard:
                        test._groups.append("standard")
                        test._groups.append("all")  # backwards compatibility

                result[modname] = (mod, tests)

    return result


def prune_tests(test_specs, test_modules, version):
    """
    Return tests matching the given test-specs and OpenFlow version
    @param test_specs A list of group names or test names.
    @param version An OpenFlow version (e.g. "1.0")
    @param test_modules Same format as the output of load_test_modules.
    @returns Same format as the output of load_test_modules.
    """
    result = {}
    for e in test_specs:
        matched = False

        if e.startswith('^'):
            negated = True
            e = e[1:]
        else:
            negated = False

        for (modname, (mod, tests)) in test_modules.items():
            for (testname, test) in tests.items():
                if e in test._groups or e == "%s.%s" % (modname, testname):
                    result.setdefault(modname, (mod, {}))
                    if not negated:
                        if (not hasattr(test, "_versions") or
                           version in test._versions):
                            result[modname][1][testname] = test
                    else:
                        if (modname in result and
                           testname in result[modname][1]):
                            del result[modname][1][testname]
                            if not result[modname][1]:
                                del result[modname]
                    matched = True

        if not matched:
            die("test-spec element %s did not match any tests" % e)

    return result


def process_list_args(test_modules):
    mod_count = 0
    test_count = 0
    all_groups = set()

    print("""
Tests are shown grouped by module.
""")
    for (modname, (mod, tests)) in test_modules.items():
        mod_count += 1
        desc = (mod.__doc__ or "No description").strip().split('\n')[0]
        print("  Module %13s: %s" % (mod.__name__, desc))

        for (testname, test) in tests.items():
            desc = (test.__doc__ or "No description").strip().split('\n')[0]

            groups = set(test._groups) - set(["all", "standard", modname])
            all_groups.update(test._groups)
            if groups:
                desc = "(%s) %s" % (",".join(groups), desc)
            if hasattr(test, "_versions"):
                desc = "(%s) %s" % (",".join(sorted(test._versions)), desc)

            start_str = " %s%s %s:" % (test._nonstandard and "*" or " ",
                                       test._disabled and "!" or " ",
                                       testname)
            print("  %22s : %s" % (start_str, desc))
            test_count += 1
        print
    print("'%d' modules shown with a total of '%d' tests\n" %
          (mod_count, test_count))
    print("Test groups: %s" % (', '.join(sorted(all_groups))))

    sys.exit(0)


def process_list_test_names_args(test_modules):
    for (modname, (mod, tests)) in test_modules.items():
        for (testname, test) in tests.items():
            print("%s.%s" % (modname, testname))

    sys.exit(0)


def sort_tests(test_modules):
    sorted_tests = []
    for (modname, (mod, tests)) in sorted(test_modules.items()):
        for (testname, test) in sorted(tests.items()):
            sorted_tests.append(test)
    return sorted_tests


def open_logfile(name):
    """
    (Re)open logfile

    When using a log directory a new logfile is created for each test. The same
    code is used to implement a single logfile in the absence of --log-dir.
    """

    _format = "%(asctime)s.%(msecs)03d  %(name)-10s: %(levelname)-8s: \
              %(message)s"
    _datefmt = "%H:%M:%S"

    if config["log_dir"] is not None:
        filename = os.path.join(config["log_dir"], name) + ".log"
    else:
        filename = config["log_file"]

    logger = logging.getLogger()

    # Remove any existing handlers
    for handler in logger.handlers:
        logger.removeHandler(handler)
        handler.close()

    # Add a new handler
    handler = logging.FileHandler(filename, mode='a')
    handler.setFormatter(logging.Formatter(_format, _datefmt))
    logger.addHandler(handler)


def die(msg, exit_val=1):
    logging.critical(msg)
    sys.exit(exit_val)
