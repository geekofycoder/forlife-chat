supabase-js 2.117.2 (MIT), browser bundle, copied unchanged from the npm package @supabase/supabase-js@2.117.2, file dist/umd/supabase.js.
npm integrity of the package tarball (checked before copying): sha512-eSG2VKnHR+Clp1PmidZ1/weJ8PJwoybjva3L2GgKqFG4YDS1Iqmc61psKGZP5xw6OMT2O7ZorPR42PY6q1BOXg==
sha256 of the file here: 59d39487c3589843b410322d8a3d562ce022aba1e5ccb16898ef3fb2a0da2ecd
Why it is here and not loaded from a CDN: the page then depends on no third-party host for sign-in code, and the version is fixed.
To update: npm pack @supabase/supabase-js@<version>, compare the tarball's sha512 with "npm view ... dist.integrity", copy dist/umd/supabase.js, change the file name and the <script> tag in static/index.html.
