cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(rtos_system_fit_zephyr)
target_sources(app PRIVATE src/main.c)
