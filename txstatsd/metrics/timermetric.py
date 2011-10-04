
from string import Template

import time

from txstatsd.metrics.histogrammetric import HistogramMetricReporter
from txstatsd.metrics.metermetric import MeterMetricReporter
from txstatsd.metrics.metric import Metric
from txstatsd.stats.exponentiallydecayingsample \
    import ExponentiallyDecayingSample


class TimerMetric(Metric):
    """
    A timer metric which aggregates timing durations and provides duration
    statistics, plus throughput statistics via L{MeterMetric}.
    """

    def __init__(self, connection, name, sample_rate=1):
        """Construct a metric that reports samples to the supplied
        C{connection}.

        @param connection: The connection endpoint representing
            the StatsD server.
        @param name: Indicates what is being instrumented.
        @param sample_rate: Restrict the number of samples sent
            to the StatsD server based on the supplied C{sample_rate}.
        """
        Metric.__init__(self, connection, name, sample_rate=sample_rate)

    def mark(self, duration):
        """Report this sample performed in duration (measured in seconds)."""
        self.send("%s|ms" % duration)


class TimerMetricReporter(object):
    """
    A timer metric which aggregates timing durations and provides duration
    statistics, plus throughput statistics via L{MeterMetricReporter}.
    """

    MESSAGE = (
        "$prefix%(key)s.min %(min)s %(timestamp)s\n"
        "$prefix%(key)s.max %(max)s %(timestamp)s\n"
        "$prefix%(key)s.mean %(mean)s %(timestamp)s\n"
        "$prefix%(key)s.stddev %(stddev)s %(timestamp)s\n"
        "$prefix%(key)s.median %(median)s %(timestamp)s\n"
        "$prefix%(key)s.75percentile %(75percentile)s %(timestamp)s\n"
        "$prefix%(key)s.95percentile %(95percentile)s %(timestamp)s\n"
        "$prefix%(key)s.98percentile %(98percentile)s %(timestamp)s\n"
        "$prefix%(key)s.99percentile %(99percentile)s %(timestamp)s\n"
        "$prefix%(key)s.999percentile %(999percentile)s %(timestamp)s\n")

    def __init__(self, name, wall_time_func=time.time, prefix=""):
        """Construct a metric we expect to be periodically updated.

        @param name: Indicates what is being instrumented.
        @param wall_time_func: Function for obtaining wall time.
        @param prefix: If present, a string to prepend to the message
            composed when C{report} is called.
        """
        self.name = name
        self.wall_time_func = wall_time_func

        if prefix:
            prefix += '.'
        self.message = Template(TimerMetricReporter.MESSAGE).substitute(
            prefix=prefix)

        sample = ExponentiallyDecayingSample(1028, 0.015)
        self.histogram = HistogramMetricReporter(sample)
        self.meter = MeterMetricReporter(
            'calls', wall_time_func=self.wall_time_func)
        self.clear()

    def clear(self):
        """Clears all recorded durations."""
        self.histogram.clear()

    def count(self):
        return self.histogram.count

    def fifteen_minute_rate(self):
        return self.meter.fifteen_minute_rate()

    def five_minute_rate(self):
        return self.meter.five_minute_rate()

    def mean_rate(self):
        return self.meter.mean_rate()

    def one_minute_rate(self):
        return self.meter.one_minute_rate()

    def max(self):
        """Returns the longest recorded duration."""
        return self.histogram.max()

    def min(self):
        """Returns the shortest recorded duration."""
        return self.histogram.min()

    def mean(self):
        """Returns the arithmetic mean of all recorded durations."""
        return self.histogram.mean()

    def std_dev(self):
        """Returns the standard deviation of all recorded durations."""
        return self.histogram.std_dev()

    def percentiles(self, *percentiles):
        """
        Returns an array of durations at the given percentiles.

        @param percentiles: One or more percentiles.
        """
        return [percentile for percentile in
            self.histogram.percentiles(*percentiles)]

    def get_values(self):
        """Returns a list of all recorded durations in the timer's sample."""
        return [value for value in self.histogram.get_values()]

    def update(self, duration):
        """Adds a recorded duration.

        @param duration: The length of the duration in seconds.
        """
        if duration >= 0:
            self.histogram.update(duration)
            self.meter.mark()

    def tick(self):
        """Updates the moving averages."""
        self.meter.tick()

    def report(self, timestamp):
        # median, 75, 95, 98, 99, 99.9 percentile
        percentiles = self.percentiles(0.5, 0.75, 0.95, 0.98, 0.99, 0.999)

        return self.message % {
            "key": self.name,
            "min": self.min(),
            "max": self.max(),
            "mean": self.mean(),
            "stddev": self.std_dev(),
            "median": percentiles[0],
            "75percentile": percentiles[1],
            "95percentile": percentiles[2],
            "98percentile": percentiles[3],
            "99percentile": percentiles[4],
            "999percentile": percentiles[5],
            "timestamp": timestamp}