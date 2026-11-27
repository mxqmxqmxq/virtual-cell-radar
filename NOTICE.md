# Notice / 致谢

Virtual Cell Radar indexes metadata and links of papers from the services below. It is an independent project and is not affiliated with or endorsed by any of them. Official references were read on 2026-11-14.

## arXiv

"Thank you to arXiv for use of its open access interoperability." (acknowledgement phrase commonly used by arXiv API users; the terms page https://info.arxiv.org/help/api/tou.html states metadata is CC0 and names no required wording, so this phrase is a courtesy, not a requirement.)
Rate-limit commitment from the same page: at most one request every three seconds, one connection at a time.

## bioRxiv and medRxiv

Preprint metadata comes from the bioRxiv/medRxiv API (https://api.biorxiv.org/), operated by Cold Spring Harbor Laboratory. Preprints are not peer reviewed. Each preprint keeps the license chosen by its authors (https://www.biorxiv.org/about/FAQ). No required acknowledgement wording was found: 待核实.

## OpenReview

Submission metadata for ICLR comes from OpenReview (https://openreview.net). Submissions remain the property of their authors; they may be under review, rejected or withdrawn. Terms: https://openreview.net/legal/terms. No required acknowledgement wording was found: 待核实.
An optional fetcher (`fetchers/openreview.py`, disabled by default) uses API v2 with an optional `OPENREVIEW_TOKEN`.

## Anthropic (Claude)

LLM annotations (scores, classifications, Chinese summaries) are generated with Claude models from Anthropic. Terms: https://www.anthropic.com/legal/consumer-terms and https://www.anthropic.com/legal/commercial-terms. No required acknowledgement wording was found; this notice is a courtesy.

## Fonts

The web page loads Geist and Geist Mono from Google Fonts at view time. Geist is by Vercel and collaborators and is stated by its repository to be under the SIL Open Font License 1.1 (https://github.com/vercel/geist-font). Font files are not bundled in this repository. The license as shown by Google Fonts: 待核实.

## Front-end code

The files in `src/aivc/web/assets/` (app.js, home*.js, home*.css, motion.css, styles.css) were checked for file headers, license statements and third-party attributions. None were found: no third-party JavaScript or CSS library is included, and the WebGL, canvas and animation code is written for this project (comments only describe measured parameters of animation behaviour). No libraries are loaded from CDNs; the only external request is the Google Fonts stylesheet above.

## News sources / 新闻来源

The business-news board links to articles from the feeds below. This project is not affiliated with or endorsed by any of them. It stores only titles, links, source names, publication times and Chinese summaries written by an LLM in its own words; article full text and long quotations are neither stored nor shown, and each item links back to the original. Feeds and terms pages were read on 2026-11-15. Rights holders can ask for removal (see `DATA_LICENSE.md`, section 5). Full per-source details are in `DATA_LICENSE.md`, section 6.

- TechCrunch (https://techcrunch.com/rss-terms-of-use/): the RSS terms require attribution to TechCrunch and a link to the full article; items are shown with source name and link.
- STAT (https://www.statnews.com/terms-conditions/): RSS is part of its stated site access; scraping/caching limits apply, so only title and link are kept. 待核实.
- GEN, Genetic Engineering & Biotechnology News (https://www.genengnews.com/legal/): no RSS clause; reproduction and mirroring are barred, so only title and link are kept. 待核实.
- Labiotech (https://www.labiotech.eu/imprint/): no terms page found. 待核实.
- Chan Zuckerberg Initiative (https://chanzuckerberg.com): terms page not retrievable. 待核实.
- Genentech Stories (https://www.gene.com): terms page not found. 待核实.
- XtalPi (https://www.xtalpi.com): terms page not read. 待核实.
- The 2026-11 expansion added the sources below. Each item links to the original article; only title, link, date and an LLM-written Chinese summary are kept. Terms conclusions are in `DATA_LICENSE.md`, section 6; unless stated otherwise their terms are marked 待核实.
  - Media feeds: BiopharmaTrend, Drug Discovery Trends, MedCity News, FDA Press Releases (US Food and Drug Administration, Stay Informed RSS).
  - Institution and company feeds: Biohub, Absci, EMBL News, Wyss Institute at Harvard, MIT News (Biological engineering and Artificial intelligence topic feeds; the MIT News terms state that RSS feeds are offered for syndication and ask for credit and a link back), Google Research Blog, Google Health Blog, Google DeepMind Blog, Microsoft Research Blog.
  - HTML news list pages (only the list page is read, for title, link and date): Arc Institute, Wellcome Sanger Institute (site content is stated to be CC BY-NC-ND 4.0; attribution given here and on each item's source name), insitro, EMBL-EBI.
- The names above are trademarks of their owners. This project is not affiliated with or endorsed by any of them.

## GitHub / 知识资源

The knowledge-resources board links to third-party repositories, tool libraries, data portals, benchmarks, courses, reviews, tutorials and blogs, maintained by many institutions and individuals. This project is not affiliated with or endorsed by any of them and is grateful to their authors and maintainers. It stores only each resource's name, a link and its own Chinese description; the content, code and data behind the links belong to their respective owners and remain under their own licenses (see `DATA_LICENSE.md`, section 7).

Repository metrics (stars, last push time, archived flag, license identifier, main language) are fetched from the GitHub REST API (https://api.github.com) with `aivc resources refresh`, anonymously or with an optional `GITHUB_TOKEN`; no required acknowledgement wording was found, and whether GitHub's terms set further conditions for this use: 待核实.

## OpenAlex

OpenAlex data, CC0 1.0 as stated by OpenAlex (the statement is taken from the project design brief; OpenAlex's own pages were not re-read in this change). The polite-pool `mailto` parameter is optional and is set by the operator; no contact address is stored in this repository. Required acknowledgement wording: 待核实.

## Europe PMC

Europe PMC, EMBL-EBI. Records are retrieved through its public REST search API; API use is subject to its terms: 待核实.

## Hugging Face Daily Papers

Daily paper listings come from the public JSON endpoint of Hugging Face Daily Papers. Paper metadata originates from arXiv. This project is not affiliated with or endorsed by Hugging Face or arXiv. Terms for the endpoint: 待核实.
