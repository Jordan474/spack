# Copyright 2013-2022 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import multiprocessing
import os

import pytest

from spack.util.cpus import cpus_available
from spack.util.environment import set_env


@pytest.fixture
def mock_cpu_count_10(monkeypatch):
    fake_cpu_count = 10

    def _sched_getaffinity(_):
        return list(range(fake_cpu_count))

    def _cpu_count():
        return fake_cpu_count

    monkeypatch.setattr(os, 'sched_getaffinity', _sched_getaffinity)
    monkeypatch.setattr(multiprocessing, 'cpu_count', _cpu_count)
    return fake_cpu_count


@pytest.mark.parametrize('omp', [True, False])
def test_cpus_available(omp, mock_cpu_count_10):
    with set_env(OMP_NUM_THREADS=None, OMP_THREAD_LIMIT=None):
        assert cpus_available(omp=omp) == 10
        with set_env(OMP_NUM_THREADS="5"):
            assert cpus_available(omp=omp) == (5 if omp else 10)
        with set_env(OMP_NUM_THREADS="42"):
            assert cpus_available(omp=omp) == (42 if omp else 10)
        with set_env(OMP_THREAD_LIMIT="5"):
            assert cpus_available(omp=omp) == (5 if omp else 10)
        with set_env(OMP_THREAD_LIMIT="42"):
            assert cpus_available(omp=omp) == 10
        with set_env(OMP_NUM_THREADS="42", OMP_THREAD_LIMIT="5"):
            assert cpus_available(omp=omp) == (5 if omp else 10)

        with set_env(OMP_NUM_THREADS="  42  ", OMP_THREAD_LIMIT="  5 "):
            assert cpus_available(omp=omp) == (5 if omp else 10)
        with set_env(OMP_NUM_THREADS="", OMP_THREAD_LIMIT="  "):
            assert cpus_available(omp=omp) == 10
        with set_env(OMP_NUM_THREADS="  BAD  ", OMP_THREAD_LIMIT="  NaN "):
            assert cpus_available(omp=omp) == 10
