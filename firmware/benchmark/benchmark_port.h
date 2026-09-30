/*
 * Tang Primer 20K CoreMark port interface.
 * Copyright 2026. SPDX-License-Identifier: BSD-2-Clause
 */
#ifndef TANG20K_BENCHMARK_PORT_H
#define TANG20K_BENCHMARK_PORT_H

#define BENCHMARK_CALIBRATION_ITERATIONS 1000u
#define BENCHMARK_TARGET_SECONDS 20u

unsigned int benchmark_elapsed_interval_count(void);
void benchmark_port_error_halt(const char *message);

#endif
