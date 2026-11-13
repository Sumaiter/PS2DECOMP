# Third-party notices and provenance

PS2DECOMP is extracted from the author's local RAC4 Werkbank 0.7.21 and
RAC3 Werkbank 0.2.2. The shared editor, queue, search rules and original adapter
logic retain the GPL-3.0-only license supplied with those tools. Original
compiler-profile evidence remains specific to its game; no game binaries or
proprietary compilers are distributed here.

The vendored `vendor/m2c_pycparser` package is the parser fork distributed by
[m2c](https://github.com/matt-kempster/m2c), derived from pycparser and PLY.
Its BSD-style license notices are retained in the package and `ply` directory.
The full m2c decompiler is not bundled; users may install it separately under
the terms of its own GPL-3.0-only license.

Runtime dependencies are installed separately: pyelftools (public domain) and
rabbitizer (MIT). Consult their installed distributions for complete notices.

`media/logo.png` is an original PS2DECOMP project image generated using the
built-in image-generation tool. Its prompt is recorded in
[media/LOGO.md](media/LOGO.md). It contains no official console logo.
