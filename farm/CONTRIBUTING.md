# Contributing to ArtisanHub Farm

ArtisanHub Farm is the print farm that ships with ArtisanHub: a fork of
[OpenPrintHQ](https://github.com/norjms/openprinthq) built to be served under the hub's name. Changes that
are not specific to ArtisanHub are best made in OpenPrintHQ itself; this fork takes them from there.

## How changes get in

1. Fork the repository and make your change on a branch.
2. Open a pull request against `main`. The checks build the web app (at the root and under `/apps/farm`),
   check and test the control plane, and build the images.
3. Sign the Contributor License Agreement. The first time you open a pull request, the CLA check comments
   with a sentence to post; once you post it, your signature covers every later pull request too. Read the
   agreement in [`CLA.md`](CLA.md) first.
4. Changes reach `main` only through a pull request with passing checks and a signed CLA.

## License

The project is under the GNU Affero General Public License, version 3 (`LICENSE`). By signing the CLA you
also allow the maintainer to offer your contribution under other terms (section 2 of the CLA).
