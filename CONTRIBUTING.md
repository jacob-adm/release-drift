# Contributing

## Tests

```sh
python3 -m unittest discover -s tests
```

The suite builds wheels, sdists and npm tarballs in temporary directories and checks archive
reading and the hostile-artifact refusals, the execution surfaces and URL hosts found in a
release, `diff` and `source` (against a temporary git repository), and `fetch`'s checks without
the network.

## Contributing

Changes reach `main` only through pull requests.

1. Fork the repository and create a branch.
2. Make the change with a test that fails without it, then run `python3 -m unittest discover -s tests`.
3. Open a pull request against `main`. CI runs the tests on Linux, macOS and Windows with Python
   3.8 and 3.13, and builds the package; the required checks must pass before merging. Pull requests
   are merged with a merge commit.

Keep release-drift standard-library-only, and keep its output byte-identical for the same inputs.
