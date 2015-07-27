# Copyright (C) 2011-2012 Canonical Services Ltd
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import re
import time
import logging

from twisted.python import log

from txstatsd.metrics.metermetric import MeterMetricReporter


SPACES = re.compile("\s+")
SLASHES = re.compile("\/+")
NON_ALNUM = re.compile("[^a-zA-Z_\-0-9\.]")
RATE = re.compile("^@([\d\.]+)")


def normalize_key(key):
    """
    Normalize a key that might contain spaces, forward-slashes and other
    special characters into something that is acceptable by graphite.
    """
    key = SPACES.sub("_", key)
    key = SLASHES.sub("-", key)
    key = NON_ALNUM.sub("", key)
    return key


class BaseMessageProcessor(object):

    def process(self, message):
        """
        """
        if not ":" in message:
            return self.fail(message)

        key, data = message.strip().split(":", 1)
        if not "|" in data:
            return self.fail(message)

        fields = data.split("|")
        if len(fields) < 2 or len(fields) > 3:
            return self.fail(message)

        key = normalize_key(key)
        metric_type = fields[1]
        return self.process_message(message, metric_type, key, fields)

    def rebuild_message(self, metric_type, key, fields):
        return key + ":" + "|".join(fields)

    def fail(self, message):
        """Log and discard malformed message."""
        log.msg("Bad line: %r" % message, logLevel=logging.DEBUG)


class MessageProcessor(BaseMessageProcessor):
    """
    This C{MessageProcessor} produces StatsD-compliant messages
    for publishing to a Graphite server.
    Metrics behaviour that varies from StatsD should be placed in
    some specialised C{MessageProcessor} (see L{ConfigurableMessageProcessor
    <txstatsd.server.configurableprocessor.ConfigurableMessageProcessor>}).
    """

    def __init__(self, time_function=time.time, plugins=None,
                 legacy_namespace=1, message_prefix="stats", internal_metrics_prefix="statsd.",
                 delete_idle_counters=0, lightweight_mode=0):
        self.time_function = time_function

        self.legacy_namespace = legacy_namespace
        self.delete_idle_counters = delete_idle_counters
        self.lightweight_mode = lightweight_mode

        self.stats_prefix = "stats."
        self.internal_metrics_prefix = "statsd."
        self.count_prefix = "stats_counts."
        self.timer_prefix = self.stats_prefix + "timers."
        self.gauge_prefix = self.stats_prefix + "gauge."

        if not legacy_namespace:
            self.stats_prefix = message_prefix + "."
            self.internal_metrics_prefix = internal_metrics_prefix
            self.count_prefix = self.stats_prefix + "counters."
            self.timer_prefix = self.stats_prefix + "timers."
            self.gauge_prefix = self.stats_prefix + "gauges."

        self.process_timings = {}
        self.by_type = {}
        self.last_flush_duration = 0
        self.last_process_duration = 0

        self.timer_metrics = {}
        self.counter_metrics = {}
        self.gauge_metrics = {}
        self.meter_metrics = {}

        self.plugins = {}
        self.plugin_metrics = {}

        if plugins is not None:
            for plugin in plugins:
                self.plugins[plugin.metric_type] = plugin

    def get_metric_names(self):
        """Return the names of all seen metrics."""
        metrics = set()
        metrics.update(self.timer_metrics.keys())
        metrics.update(self.counter_metrics.keys())
        metrics.update(self.gauge_metrics.keys())
        metrics.update(self.meter_metrics.keys())
        metrics.update(self.plugin_metrics.keys())
        return list(metrics)

    def process_message(self, message, metric_type, key, fields):
        """
        Process a single entry, adding it to either C{counters}, C{timers},
        or C{gauge_metrics} depending on which kind of message it is.
        """
        start = self.time_function()
        if metric_type == "c":
            self.process_counter_metric(key, fields, message)
        elif metric_type == "ms":
            self.process_timer_metric(key, fields[0], message)
        elif metric_type == "g":
            self.process_gauge_metric(key, fields[0], message)
        elif metric_type == "m":
            self.process_meter_metric(key, fields[0], message)
        elif metric_type in self.plugins:
            self.process_plugin_metric(metric_type, key, fields, message)
        else:
            return self.fail(message)
        self.process_timings.setdefault(metric_type, 0)
        self.process_timings[metric_type] += self.time_function() - start
        self.by_type.setdefault(metric_type, 0)
        self.by_type[metric_type] += 1

    def get_message_prefix(self, kind):
        return "stats." + kind

    def process_plugin_metric(self, metric_type, key, items, message):
        if not key in self.plugin_metrics:
            factory = self.plugins[metric_type]
            metric = factory.build_metric(
                self.get_message_prefix(factory.name),
                name=key, wall_time_func=self.time_function)
            self.plugin_metrics[key] = metric
        self.plugin_metrics[key].process(items)

    def process_timer_metric(self, key, duration, message):
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            return self.fail(message)

        self.compose_timer_metric(key, duration)

    def compose_timer_metric(self, key, duration):
        if key not in self.timer_metrics:
            self.timer_metrics[key] = []
        self.timer_metrics[key].append(duration)

    def process_counter_metric(self, key, composite, message):
        try:
            value = float(composite[0])
        except (TypeError, ValueError):
            return self.fail(message)
        rate = 1
        if len(composite) == 3:
            match = RATE.match(composite[2])
            if match is None:
                return self.fail(message)
            rate = match.group(1)

        self.compose_counter_metric(key, value, rate)

    def compose_counter_metric(self, key, value, rate):
        if key not in self.counter_metrics:
            self.counter_metrics[key] = 0
        try:
            self.counter_metrics[key] += value * (1 / float(rate))
        except KeyError: # in case a flush just cleared the keys 
            self.counter_metrics[key] = value * (1 / float(rate))

    def process_gauge_metric(self, key, composite, message):
        values = composite.split(":")
        if not len(values) == 1:
            return self.fail(message)

        try:
            value = float(values[0])
        except (TypeError, ValueError):
            self.fail(message)

        self.compose_gauge_metric(key, value)

    def compose_gauge_metric(self, key, value):
        self.gauge_metrics[key] = value

    def process_meter_metric(self, key, composite, message):
        values = composite.split(":")
        if not len(values) == 1:
            return self.fail(message)

        try:
            value = float(values[0])
        except (TypeError, ValueError):
            self.fail(message)

        self.compose_meter_metric(key, value)

    def compose_meter_metric(self, key, value):
        if not key in self.meter_metrics:
            metric = MeterMetricReporter(key, self.time_function,
                                         prefix="stats.meter")
            self.meter_metrics[key] = metric
        self.meter_metrics[key].mark(value)

    def flush(self, interval=10000, percent=90):
        """
        Flush all queued stats, computing a normalized count based on
        C{interval} and mean timings based on C{threshold}.
        """
        per_metric = {}
        num_stats = 0
        interval = interval / 1000
        timestamp = int(self.time_function())

        start = self.time_function()
        events = 0
        for metrics in self.flush_counter_metrics(interval, timestamp):
            for metric in metrics:
                yield metric
            events += 1
        duration = self.time_function() - start
        num_stats += events
        per_metric["counter"] = (events, duration)

        start = self.time_function()
        events = 0
        for metrics in self.flush_timer_metrics(percent, timestamp):
            for metric in metrics:
                yield metric
            events += 1
        duration = self.time_function() - start
        num_stats += events
        per_metric["timer"] = (events, duration)

        start = self.time_function()
        events = 0
        for metrics in self.flush_gauge_metrics(timestamp):
            for metric in metrics:
                yield metric
            events += 1
        duration = self.time_function() - start
        num_stats += events
        per_metric["gauge"] = (events, duration)

        start = self.time_function()
        events = 0
        for metrics in self.flush_meter_metrics(timestamp):
            for metric in metrics:
                yield metric
            events += 1
        duration = self.time_function() - start
        num_stats += events
        per_metric["meter"] = (events, duration)

        start = self.time_function()
        events = 0
        for metrics in self.flush_plugin_metrics(interval, timestamp):
            for metric in metrics:
                yield metric
            events += 1
        duration = self.time_function() - start
        num_stats += events
        per_metric["plugin"] = (events, duration)

        for metrics in self.flush_metrics_summary(num_stats, per_metric,
                                                  timestamp):
            for metric in metrics:
                yield metric

    def flush_counter_metrics(self, interval, timestamp):
        for key, count in self.counter_metrics.iteritems():
            self.counter_metrics[key] = 0

            value = count / interval
            if not self.legacy_namespace:
                output = ((self.count_prefix + key + ".rate", value, timestamp),
                          (self.count_prefix + key + ".count", count, timestamp))
            else:
                output = ((self.stats_prefix + key, value, timestamp),
                          (self.count_prefix + key, count, timestamp))
            if self.lightweight_mode:
                yield output[1:2]
            else:
                yield output
        # clear all keys on each flush to avoid processing zeros.
        if self.delete_idle_counters:
            self.counter_metrics = {}

    def flush_timer_metrics(self, percent, timestamp):
        threshold_value = ((100 - percent) / 100.0)
        for key, timers in self.timer_metrics.iteritems():
            count = len(timers)
            if count > 0:
                self.timer_metrics[key] = []

                timers.sort()
                lower = timers[0]
                upper = timers[-1]
                count = len(timers)

                mean = lower
                threshold_upper = upper

                if count > 1:
                    index = count - int(round(threshold_value * count))
                    timers = timers[:index]
                    threshold_upper = timers[-1]
                    mean = sum(timers) / index

                items = {".mean": mean,
                         ".upper": upper,
                         ".upper_%s" % percent: threshold_upper,
                         ".lower": lower}
                if not self.lightweight_mode:
                    items[".count"] = count
                yield sorted((self.timer_prefix + key + item, value, timestamp)
                             for item, value in items.iteritems())

    def flush_gauge_metrics(self, timestamp):
        for key, value in self.gauge_metrics.iteritems():
            yield ((self.gauge_prefix + key + ".value", value, timestamp),)

    def flush_meter_metrics(self, timestamp):
        for metric in self.meter_metrics.itervalues():
            messages = metric.report(timestamp)
            yield messages

    def flush_plugin_metrics(self, interval, timestamp):
        for metric in self.plugin_metrics.itervalues():
            messages = metric.flush(interval, timestamp)
            yield messages

    def flush_metrics_summary(self, num_stats, per_metric, timestamp):
        yield ((self.internal_metrics_prefix + "numStats",
                num_stats, timestamp),)

        self.last_flush_duration = 0
        for name, (value, duration) in per_metric.iteritems():
            yield ((self.internal_metrics_prefix +
                    "flush.%s.count" % name,
                    value, timestamp),
                   (self.internal_metrics_prefix +
                    "flush.%s.duration" % name,
                    duration * 1000, timestamp))
            log.msg("Flushed %d %s metrics in %.6f" %
                    (value, name, duration))
            self.last_flush_duration += duration

        self.last_process_duration = 0
        for metric_type, duration in self.process_timings.iteritems():
            yield ((self.internal_metrics_prefix +
                    "receive.%s.count" %
                    metric_type, self.by_type[metric_type], timestamp),
                   (self.internal_metrics_prefix +
                    "receive.%s.duration" %
                    metric_type, duration * 1000, timestamp))
            log.msg("Processing %d %s metrics took %.6f" %
                    (self.by_type[metric_type], metric_type, duration))
            self.last_process_duration += duration

        self.process_timings.clear()
        self.by_type.clear()
