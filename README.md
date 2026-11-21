# RN SUS · Blast Radius

The container selects SQLGlot tests from baseline line coverage, fixture reads, expression types and observed operand types. It applies each diff independently to source text from the pinned GitHub commit. For every candidate selection it tries one through eight workers and keeps the lowest measured cost using longest-first scheduling.

The scripts used to create the maps take a pristine checkout and the test inventory. All maps use baseline observations. The image reads only the maps, baseline source and runtime input. It never receives sample failure labels.

## Reproducing the maps

Use Python 3.12+ with pytest and SQLGlot's test dependencies. Set ROOT to the unpacked pinned checkout and TESTS to the official tests.json. Run from this repository:

    python build_map.py "$ROOT" "$TESTS" map.json.gz
    python build_classes.py "$ROOT" "$TESTS" classes.json.gz
    python build_types.py "$ROOT" types.json.gz

The map builders run the unmodified baseline tests with instrumentation. Worker calls run in the tracing process with arguments and results serialized to capture dependencies from their execution. The runtime maps test names to the current numeric IDs. Baseline execution coverage is an estimate of possible effects on unseen changes.

For the local Makefile, place the extracted official starter in a sibling directory named official. Use make sample with Docker, make and Python 3 to score.
