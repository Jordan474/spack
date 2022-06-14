# Copyright 2013-2022 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import multiprocessing
import os


def cpus_available(omp=True):
    """
    Returns the number of CPUs available for the current process, or the number
    of phyiscal CPUs when that information cannot be retrieved. The number
    of available CPUs might differ from the number of physical CPUs when
    using spack through Slurm or container runtimes.

    Args:
        omp (bool): support OMP_NUM_THREADS and OMP_THREAD_LIMIT, like nproc
    """
    # https://github.com/coreutils/gnulib/blob/master/lib/nproc.c

    def get_omp_var(name, default):
        try:
            val = int(os.environ[name].strip())
            if val <= 0:
                # Ignore zero or negative, see nproc.c
                return default
        except KeyError:
            return default
        except ValueError:
            # Ignore non-integers
            return default
        else:
            return val

    limit = None
    if omp:
        limit = get_omp_var('OMP_THREAD_LIMIT', None)
        ompnum = get_omp_var('OMP_NUM_THREADS', None)
        if ompnum:
            return min(ompnum, limit) if limit else ompnum
    try:
        cpus = len(os.sched_getaffinity(0))  # novermin
    except Exception:
        cpus = multiprocessing.cpu_count()
    return min(cpus, limit) if limit else cpus
