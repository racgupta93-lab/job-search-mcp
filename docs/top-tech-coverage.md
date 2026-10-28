# Top tech companies: what this tool can and cannot search

Checked on 2026-10-27 for **127 well-known tech companies**. The list is my own selection of large and well-known US tech employers, not an official ranking.

**Two tiers.** *Official feeds*: Greenhouse, Lever, Ashby and SmartRecruiters publish documented, public, read-only posting APIs. *Workday (unofficial)*: Workday has no public API, so those sites are read through the address their own career pages use, at the owner's explicit request, with robots.txt respected. Company career websites themselves are **not** scraped.

**Result: 80 of 127 are searchable** (60 through official feeds, 20 through Workday). 45 have no usable route, and 2 more have a public board with only a token posting or two.

## Searchable (60)

| Company | System | Board name | Open jobs when checked |
|---|---|---|---|
| Affirm | greenhouse | `affirm` | 242 |
| Airbnb | greenhouse | `airbnb` | 194 |
| Analog Devices | ashby | `analog` | 27 |
| Anthropic | greenhouse | `anthropic` | 681 |
| Arista Networks | smartrecruiters | `aristanetworks` | 244 |
| Asana | greenhouse | `asana` | 113 |
| Block | greenhouse | `block` | 202 |
| Brex | greenhouse | `brex` | 261 |
| C3.ai | greenhouse | `c3iot` | 91 |
| Canva | smartrecruiters | `canva` | 78 |
| Carvana | greenhouse | `carvana` | 1660 |
| Chime | greenhouse | `chime` | 72 |
| Cloudflare | greenhouse | `cloudflare` | 461 |
| Coinbase | greenhouse | `coinbase` | 227 |
| Databricks | greenhouse | `databricks` | 898 |
| Datadog | greenhouse | `datadog` | 424 |
| Discord | greenhouse | `discord` | 46 |
| Dropbox | greenhouse | `dropbox` | 22 |
| Duolingo | greenhouse | `duolingo` | 58 |
| Elastic | greenhouse | `elastic` | 404 |
| Figma | greenhouse | `figma` | 142 |
| GitLab | greenhouse | `gitlab` | 236 |
| GoDaddy | greenhouse | `godaddy` | 29 |
| HP | greenhouse | `hp` | 22 |
| Instacart | greenhouse | `instacart` | 94 |
| LinkedIn | lever | `linkedin` | 23 |
| Lucid Motors | greenhouse | `lucidmotors` | 443 |
| Lyft | greenhouse | `lyft` | 178 |
| MongoDB | greenhouse | `mongodb` | 363 |
| Notion | ashby | `notion` | 139 |
| Okta | greenhouse | `okta` | 387 |
| OpenAI | ashby | `openai` | 801 |
| Palantir | lever | `palantir` | 306 |
| Peloton | greenhouse | `peloton` | 57 |
| Pinterest | greenhouse | `pinterest` | 160 |
| Plaid | ashby | `plaid` | 129 |
| Pure Storage | greenhouse | `purestorage` | 363 |
| Ramp | ashby | `ramp` | 172 |
| Reddit | greenhouse | `reddit` | 160 |
| Robinhood | greenhouse | `robinhood` | 158 |
| Roblox | greenhouse | `roblox` | 269 |
| Rubrik | greenhouse | `rubrik` | 149 |
| Samsara | greenhouse | `samsara` | 320 |
| Samsung Research America | greenhouse | `samsungresearchamerica` | 25 |
| Scale AI | greenhouse | `scaleai` | 169 |
| ServiceNow | smartrecruiters | `servicenow` | 732 |
| Snowflake | ashby | `snowflake` | 361 |
| SpaceX | greenhouse | `spacex` | 2833 |
| Spotify | lever | `spotify` | 89 |
| Squarespace | greenhouse | `squarespace` | 27 |
| Stripe | greenhouse | `stripe` | 715 |
| Take-Two | greenhouse | `taketwo` | 37 |
| Toast | greenhouse | `toast` | 338 |
| Twilio | greenhouse | `twilio` | 133 |
| Twitch | greenhouse | `twitch` | 62 |
| UiPath | ashby | `uipath` | 104 |
| Veeva | lever | `veeva` | 906 |
| Vercel | greenhouse | `vercel` | 94 |
| Western Digital | smartrecruiters | `westerndigital` | 346 |
| Zscaler | greenhouse | `zscaler` | 358 |

## Searchable through Workday (unofficial) (20)

| Company | Workday entry in companies.yaml |
|---|---|
| Accenture | `accenture.wd103.AccentureCareers` |
| Adobe | `adobe.wd5.external_experienced` |
| Autodesk | `autodesk.wd1.Ext` |
| Broadcom | `broadcom.wd1.External_Career` |
| Cadence | `cadence.wd1.External_Careers` |
| CrowdStrike | `crowdstrike.wd5.crowdstrikecareers` |
| F5 | `ffive.wd5.f5jobs` |
| HPE | `hpe.wd5.Jobsathpe` |
| Intel | `intel.wd1.External` |
| KLA | `kla.wd1.Search` |
| Marvell | `marvell.wd1.MarvellCareers` |
| Micron | `micron.wd1.External` |
| Motorola Solutions | `motorolasolutions.wd5.Careers` |
| Nvidia | `nvidia.wd5.NVIDIAExternalCareerSite` |
| Salesforce | `salesforce.wd12.External_Career_Site` |
| Slack | `salesforce.wd12.Slack` |
| Workday | `workday.wd5.Workday` |
| Zendesk | `zendesk.wd1.zendesk` |
| Zillow | `zillow.wd5.Zillow_Group_External` |
| Zoom | `zoom.wd5.Zoom` |

## A board exists but has almost nothing in it

These companies have a public board on a supported system with only a handful of postings; they hire through their own sites, so searching them is not useful.

* Uber: smartrecruiters 'uber' (1 job)
* Wayfair: smartrecruiters 'Wayfair' (1 job)

## Not searchable (45)

Platform was detected from the careers page and may be imprecise; the links are the pages I checked and may have moved. To look at these companies, use their own careers sites.

| Company | Careers platform | Careers page |
|---|---|---|
| AMD | iCIMS | https://careers.amd.com/careers-home/jobs |
| Akamai | own site (no standard platform detected) | https://akamaicareers.inflightcloud.com/ |
| Alphabet / Google | own site (no standard platform detected) | https://www.google.com/about/careers/applications/jobs/results |
| Amazon | own site (no standard platform detected) | https://www.amazon.jobs/en/ |
| Apple | own site (no standard platform detected) | https://jobs.apple.com/en-us/search |
| Applied Materials | Eightfold | https://careers.appliedmaterials.com/careers |
| Arm | iCIMS | https://careers.arm.com/ |
| Atlassian | own site (no standard platform detected) | https://www.atlassian.com/company/careers |
| Booking Holdings | iCIMS | https://jobs.booking.com/ |
| Box | own site (no standard platform detected) | https://www.box.com/about-us/careers |
| Cisco | Phenom | https://jobs.cisco.com/ |
| Cognizant | own site (no standard platform detected) | https://careers.cognizant.com/ |
| Confluent | own site (no standard platform detected) | https://careers.confluent.io/ |
| Dell | own site (no standard platform detected) | https://jobs.dell.com/ |
| DoorDash | own site (no standard platform detected) | https://careers.doordash.com/ |
| Electronic Arts | own site (no standard platform detected) | https://ea.gr8people.com/ |
| Etsy | own site (no standard platform detected) | https://careers.etsy.com/ |
| Fiserv | Phenom | https://careers.fiserv.com/ |
| Fortinet | site did not respond to an automated request | https://edel.fortinet.com/careers |
| HubSpot | own site (no standard platform detected) | https://www.hubspot.com/careers |
| IBM | own site (no standard platform detected) | https://www.ibm.com/careers/search |
| Intuit | own site (no standard platform detected) | https://jobs.intuit.com/ |
| Juniper Networks | Workday / Phenom | https://www.juniper.net/us/en/company/careers.html |
| Lam Research | Eightfold / SAP SuccessFactors | https://careers.lamresearch.com/ |
| Meta | Workday | https://www.metacareers.com/jobs |
| Microsoft | own site (no standard platform detected) | https://careers.microsoft.com/ |
| NetApp | Eightfold / SAP SuccessFactors | https://careers.netapp.com/ |
| Netflix | own site (no standard platform detected) | https://jobs.netflix.com/ |
| Nutanix | own site (no standard platform detected) | https://www.nutanix.com/company/careers |
| Oracle | Oracle Cloud HCM | https://careers.oracle.com/ |
| Palo Alto Networks | own site (no standard platform detected) | https://jobs.paloaltonetworks.com/ |
| PayPal | Eightfold | https://paypal.eightfold.ai/careers |
| Qualcomm | Eightfold | https://careers.qualcomm.com/careers |
| Rivian | iCIMS | https://careers.rivian.com/ |
| Seagate | Workday | https://seagate.wd1.myworkdayjobs.com/SeagateCareers |
| Shopify | own site (no standard platform detected) | https://www.shopify.com/careers |
| Snap | own site (no standard platform detected) | https://careers.snap.com/ |
| Sony Interactive | Greenhouse (board name not found) | https://sonyinteractive.com/en/careers/ |
| Splunk | Phenom | https://www.splunk.com/en_us/careers.html |
| Synopsys | Avature | https://careers.synopsys.com/ |
| Tesla | own site (no standard platform detected) | https://www.tesla.com/careers/search/ |
| Texas Instruments | Oracle Cloud HCM | https://careers.ti.com/ |
| Unity | own site (no standard platform detected) | https://careers.unity.com/ |
| Yelp | Phenom | https://www.yelp.careers/ |
| eBay | own site (no standard platform detected) | https://jobs.ebayinc.com/ |

### Why these are not included

* **Workday** (3 of those not found above): these use Workday but I could not verify a working career-site address for them (or they are not in the starter list). If you know the address (tenant.wdN.Site), you can add it under `workday:`.
* **Phenom, iCIMS, Eightfold, Oracle, SAP, Avature and similar** (17 here): no public, documented posting feed.
* **Own sites** such as Apple, Google, Amazon, Microsoft, Meta, Netflix (25 here): proprietary; no documented public feed, and scraping websites is not done by this tool.

If a company you care about is missing, check its careers page: if its address looks like `boards.greenhouse.io/NAME`, `jobs.lever.co/NAME`, `jobs.ashbyhq.com/NAME` or `jobs.smartrecruiters.com/NAME`, add NAME to your `companies.yaml`.
