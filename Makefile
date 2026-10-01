.PHONY: setup doctor build load run compare test benchmark-build benchmark-run benchmark-report ddr-test-build ddr-test-run ddr-test-report cpu-candidate-build cpu-candidate-run clean

setup:
	python3 scripts/setup.py

doctor:
	python3 scripts/doctor.py

build:
	python3 scripts/project.py build $(or $(PROFILE),minimal) $(or $(MEMORY),onchip)

load:
	python3 scripts/project.py load $(or $(PROFILE),minimal) $(or $(MEMORY),onchip)

run:
	python3 scripts/project.py run $(or $(PROFILE),minimal) $(PORT) $(or $(MEMORY),onchip)

compare:
	python3 scripts/project.py compare $(or $(MEMORY),onchip)

test:
	python3 scripts/project.py test

benchmark-build:
	python3 scripts/project.py benchmark-build $(or $(MEMORY),onchip)

benchmark-run:
	python3 scripts/project.py benchmark-run $(or $(PROFILE),standard) $(PORT) $(or $(MEMORY),onchip)

benchmark-report:
	python3 scripts/project.py benchmark-report

ddr-test-build:
	python3 scripts/project.py ddr-test-build $(or $(PROFILE),ALL) $(or $(STRESS_SECONDS),1800)

ddr-test-run:
	python3 scripts/project.py ddr-test-run $(or $(PROFILE),ALL) $(PORT) $(or $(TRAINING_RUNS),10) $(or $(STRESS_SECONDS),1800)

ddr-test-report:
	python3 scripts/project.py ddr-test-report

cpu-candidate-build:
	python3 scripts/project.py cpu-candidate-build

cpu-candidate-run:
	python3 scripts/project.py cpu-candidate-run $(PORT)

clean:
	rm -rf build
