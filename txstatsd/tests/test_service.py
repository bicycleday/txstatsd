
import ConfigParser
import tempfile
from unittest import TestCase

from twisted.internet.defer import inlineCallbacks, Deferred
from twisted.internet.protocol import DatagramProtocol
from twisted.internet.test.reactormixins import ReactorBuilder

from txstatsd import service
from txstatsd.server.processor import MessageProcessor
from txstatsd.server.protocol import StatsDServerProtocol


class GlueOptionsTestCase(TestCase):

    def test_defaults(self):
        """
        Defaults get passed over to the instance.
        """
        class TestOptions(service.OptionsGlue):
            optParameters = [["test", "t", "default", "help"]]
    
        o = TestOptions()
        o.parseOptions([])
        self.assertEquals("default", o["test"])
    
    def test_set_parameter(self):
        """
        A parameter can be set from the command line
        """
        class TestOptions(service.OptionsGlue):
            optParameters = [["test", "t", "default", "help"]]
    
        o = TestOptions()
        o.parseOptions(["--test", "notdefault"])
        self.assertEquals("notdefault", o["test"])
    
    def test_no_config_option(self):
        """
        A parameter can be set from the command line
        """
        class TestOptions(service.OptionsGlue):
            optParameters = [["config", "c", "default", "help"]]
    
        self.assertRaises(ValueError, lambda: TestOptions())

    def get_file_parser(self, glue_parameters_config=None, **kwargs):
        """
        Create a simple option parser that reads from disk.
        """
        if glue_parameters_config is None:
            glue_parameters_config = [["test", "t", "default", "help"]]
        f = tempfile.NamedTemporaryFile()

        config = ConfigParser.RawConfigParser()
        config.add_section('statsd')
        if not kwargs:
            config.set('statsd', 'test', 'configvalue')
        else:
            for k, v in kwargs.items():
                config.set('statsd', k, v)
        config.write(f)
        f.flush()

        class TestOptions(service.OptionsGlue):
            optParameters = glue_parameters_config

            def __init__(self):
                self.config_section = 'statsd'
                super(TestOptions, self).__init__()

        return f, TestOptions()

    def test_reads_from_config(self):
        """
        A parameter can be set from the config file.
        """
        f, o = self.get_file_parser()
        o.parseOptions(["--config", f.name])
        self.assertEquals("configvalue", o["test"])

    def test_cmdline_overrides_config(self):
        """
        A parameter from the cmd line overrides the config.
        """
        f, o = self.get_file_parser()
        o.parseOptions(["--config", f.name, "--test", "cmdline"])
        self.assertEquals("cmdline", o["test"])

    def test_ensure_config_values_coerced(self):
        """
        Parameters come out of config files casted properly.
        """
        f, o = self.get_file_parser([["number", "n", 5, "help", int]],
            number=10)
        o.parseOptions(["--config", f.name])
        self.assertEquals(10, o["number"])

    def test_support_default_not_in_config(self):
        """
        Parameters not in config files still produce a lookup in defaults.
        """
        f, o = self.get_file_parser([["number", "n", 5, "help", int]])
        o.parseOptions(["--config", f.name])
        self.assertEquals(5, o["number"])


class Agent(DatagramProtocol):

    def __init__(self):
        self.monitor_response = None

    def datagramReceived(self, data, (host, port)):
        self.monitor_response = data


class ServiceTestsBuilder(ReactorBuilder):

    def test_service(self):
        """
        The StatsD service can be created.
        """
        o = service.StatsDOptions()
        s = service.createService(o)
        self.assertTrue(isinstance(s, service.MultiService))

    def test_monitor_response(self):
        """
        The StatsD service messages the expected response to the
        monitoring agent.
        """
        reactor = self.buildReactor()

        options = service.StatsDOptions()
        processor = MessageProcessor()
        statsd_server_protocol = StatsDServerProtocol(
            processor,
            monitor_message=options["monitor-message"],
            monitor_response=options["monitor-response"])
        reactor.listenUDP(options["listen-port"], statsd_server_protocol)

        agent = Agent()
        reactor.listenUDP(0, agent)

        @inlineCallbacks
        def exercise():
            def monitor_send():
                agent.transport.write(
                    options["monitor-message"],
                    ("127.0.0.1", options["listen-port"]))

            def statsd_response(result):
                self.assertEqual(options["monitor-response"],
                                 agent.monitor_response)

            yield monitor_send()

            d = Deferred()
            d.addCallback(statsd_response)
            reactor.callLater(.1, d.callback, None)
            try:
                yield d
            except:
                raise
            finally:
                reactor.stop()

        reactor.callWhenRunning(exercise)
        self.runReactor(reactor)

globals().update(ServiceTestsBuilder.makeTestCaseClasses())
