# Matt King Photography

A fast, accessible, static marketing site for Matt King Photography in Munford, Tennessee. It is built with Astro and designed around a guided portrait experience for families, children, and high-school seniors.

## Local development

```sh
npm ci
npm run dev
```

When the project is stored in pCloud Drive on macOS, copy it to a local filesystem before installing dependencies or building. The pCloud virtual mount can reject the package-executable symlinks created by npm. Keep the pCloud copy as the source of truth, then copy source changes—not `node_modules`—between the two locations.

Run the production verification with:

```sh
npm run build
```

The generated site is written to `dist/`.

## Deployment

Netlify is the recommended host because the inquiry form uses Netlify Forms. If this directory lives inside a larger repository, set the Netlify base directory to `matt king`. The included `netlify.toml`, security headers, legacy redirects, sitemap, and form handling will then work without additional configuration.

On another static host, connect the form to that host's form service or a serverless endpoint before launch.

## Content and launch checklist

- Confirm Matt's current pricing language and, ideally, add a starting session fee or realistic typical investment range.
- Confirm copyright and current model releases for every portfolio image.
- Confirm permission and the original source for each testimonial.
- Submit a test inquiry on the production domain and verify its email notification.
- Connect the production domain only after the existing compromised hosting account has been secured, credentials rotated, unknown administrator accounts removed, and DNS records reviewed.
- Review Analytics and Search Console ownership before cutover.

## Research

The `research/` directory contains the collection and analysis scripts plus the aggregate findings used for this build. Raw crawl and discovery outputs stay local and are intentionally excluded from Git.
