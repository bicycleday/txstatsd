import os
import psutil

from twisted.internet import defer, fdesc, error
from twisted.python import log


MEMINFO_KEYS = ("MemTotal:", "MemFree:", "Buffers:",
                "Cached:", "SwapCached:", "SwapTotal:",
                "SwapFree:")

MULTIPLIERS = {"kB": 1024, "mB": 1024 * 1024}


def load_file(filename):
    """Load a file into memory with non blocking reads."""

    fd = os.open(filename, os.O_RDONLY)
    fdesc.setNonBlocking(fd)

    chunks = []
    d = defer.Deferred()

    def read_loop(data=None):
        """Inner loop."""
        if data is not None:
            chunks.append(data)
        r = fdesc.readFromFD(fd, read_loop)
        if isinstance(r, error.ConnectionDone):
            os.close(fd)
            d.callback("".join(chunks))
        elif r is not None:
            os.close(fd)
            d.errback(r)

    read_loop("")
    return d


def parse_meminfo(data, prefix="meminfo."):
    """Parse data from /proc/meminfo."""
    result = {}

    for line in data.split("\n"):
        if not line:
            continue
        parts = [x for x in line.split(" ") if x]
        if not parts[0] in MEMINFO_KEYS:
            continue

        multiple = 1

        if len(parts) == 3:
            multiple = MULTIPLIERS[parts[2]]

        # remove ':'
        label = parts[0][:-1]
        amount = int(parts[1]) * multiple
        result[prefix + label] = amount

    return result


def parse_loadavg(data, prefix="loadavg."):
    """Parse data from /proc/loadavg."""
    return dict(zip(
        (prefix + "oneminute",
         prefix + "fiveminutes",
         prefix + "fifthteenminutes"),
        [float(x) for x in data.split()[:3]]))


def report_self_stat(process=psutil.Process(os.getpid()), prefix="self.stat."):
    vsize, rss = process.get_memory_info()
    utime, stime = process.get_cpu_times()
    return {prefix + "cpu.percent": process.get_cpu_percent(),
            prefix + "cpu.user": utime,
            prefix + "cpu.system": stime,
            prefix + "memory.percent": process.get_memory_percent(),
            prefix + "memory.vsize": vsize,
            prefix + "memory.rss": rss}

def report_system_stat(prefix="stat."):
    cpu_times = psutil.cpu_times()
    return {prefix + "cpu.idle": cpu_times.idle,
            prefix + "cpu.iowait": cpu_times.iowait,
            prefix + "cpu.irq": cpu_times.irq,
            prefix + "cpu.nice": cpu_times.nice,
            prefix + "cpu.system": cpu_times.system,
            prefix + "cpu.user": cpu_times.user}


PROCESS_STATS = ((None, report_self_stat),)

SYSTEM_STATS = (("/proc/meminfo", parse_meminfo),
                ("/proc/loadavg", parse_loadavg),
                (None, report_system_stat),) + PROCESS_STATS


def send_metrics(metrics, meter):
    """Put a dict of values in stats."""
    for name, value in metrics.items():
        meter.increment(name, value)


def report_stats(stats, meter):
    """
    Read C{filename} then call C{function} to parse the contents, then report
    to C{StatsD}.
    """

    deferreds = []
    for filename, func in stats:
        if filename is not None:
            name = filename
            deferred = load_file(filename)
            deferred.addCallback(func)
        else:
            name = func.func_name
            deferred = defer.maybeDeferred(func)

        deferred.addCallback(send_metrics, meter)
        deferred.addErrback(lambda failure: log.err(
            failure, "Error while processing %s" % name))
        deferreds.append(deferred)

    return defer.DeferredList(deferreds)
