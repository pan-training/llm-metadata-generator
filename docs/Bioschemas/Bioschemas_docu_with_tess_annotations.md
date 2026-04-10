# Bioschemas — TeSS-focused reference

Purpose: a concise, ingestor-focused reference documenting exactly what the Bioschemas/RDF extractors produce and how the TeSS ingestor processes those values before turning them into Events or Materials.

Scope: ingestion pipeline only — extractor output keys, in-ingestor conversions, sanitisation (controller strong params), deduplication, scoring, and other behaviours that affect what TeSS actually imports. Generator/export behaviour is intentionally omitted.

---

## General notes
- TeSS uses the `tess_rdf_extractors` gem to parse JSON-LD / RDFa / RDF/XML. Public repository: https://github.com/ElixirTeSS/TeSS_RDF_Extractors.
- Extractors build an RDF graph and normalise schema.org HTTPS URIs to HTTP when parsing.
 - The Dublin Core property `dct:conformsTo` (http://purl.org/dc/terms/conformsTo) is used to identify the Bioschemas profile (e.g., `https://bioschemas.org/profiles/TrainingMaterial/1.0-RELEASE`). Presence of `dct:conformsTo` is recommended but not strictly required for extraction.
 - High-level ingestion flow (what actually happens when the `Bioschemas` ingestor runs):

   1. A `Tess::Rdf::*Extractor` parses the source (JSON-LD / RDFa / RDF/XML) and emits Ruby Hashes of extracted parameters (see "Extractor output keys" and examples below). Extractors normalise predicates and call helpers (`remove_blanks`, `parse_value`, `extract_people`, etc.).
   2. Extractors call `remove_blanks` to drop empty values (nil, '', []).
   3. `Ingestors::BioschemasIngestor#read_content` collects extractor outputs and yields each Hash to `convert_params`.
   4. `BioschemasIngestor#convert_params` performs minimal transformations today: it normalises `:description` via `Ingestor#convert_description` (HTML-to-Markdown conversion when HTML-like tags are present). This is the recommended place to add other per-ingestor normalisations.
   5. Extracted resource hashes are deduplicated using `BioschemasIngestor#deduplicate` (keeps the resource with the highest `metadata_score`).
   6. Each deduplicated Hash is passed to `add_event` (for Event/Course/CourseInstance) or `add_material` (for LearningResource/TrainingMaterial). When these methods receive a Hash they call the corresponding controller's strong-parameter sanitiser (`EventsController#event_params` or `MaterialsController#material_params`), wrap the result in an `OpenStruct`, run `AutoParsing#auto_parse` for configured variables, and append to `@events` / `@materials`.
   7. Later `Ingestor#write` runs `write_resources`, which finds existing resources (`check_exists`), updates or creates records (`update_resource`/`type.new`), sets defaults (`set_resource_defaults`), validates and saves. `update_resource` respects locked fields via `FieldLock.strip_locked_fields` and `set_resource_defaults` marks `scraper_record = true` and updates `last_scraped`.

---

## Property-by-property (type-agnostic first)

- `@id` / `identifier`
  - Schema: IRI or PropertyValue. TeSS: `Extraction` will extract `schema:identifier`. If the extracted identifier matches a DOI pattern (`10.<digits>/...`) it is stored in the `:doi` key and later mapped to `identifier` in JSON-LD output for materials.
  - Notes: TeSS treats DOI specially (regex detection) — include a DOI if available.

- `dct:conformsTo` (http://purl.org/dc/terms/conformsTo)
  - Schema: IRI pointing to Bioschemas profile version.
  - TeSS: used to determine which Bioschemas profile the markup conforms to; required for clear profile identification in exports and recommended for ingestion.

- `@type` / Schema.org class
  - Expected: `LearningResource`, `Course`, `CourseInstance`, `Event` (among others).
  - TeSS: Extractors run type-specific queries (e.g., RDF::Vocab::SCHEMA.LearningResource) to find resources of these types.

- `url`
  - Expected: canonical URL of the resource.
  - TeSS: extracted into `:url`; if absent and resource is an RDF::URI, the extractor uses the resource URI.

- `name` / `title`
  - Expected: human-readable title. Mapped to `:title` in extractor output and to `name` in generator output.

- `description`
  - Expected: text or HTML. TeSS: parsed and trimmed; `convert_params` normalises descriptions. Long HTML may be converted to plain text by extractor `parse_value` handling of RDF::Literal::HTML.

- `keywords`
  - Expected: list of strings or comma-separated string.
  - TeSS: `extract_keyword_like` returns array; if single comma string, it splits on commas.

- `inLanguage`
  - Expected: language code or Language object.
  - TeSS: `extract_language` returns the first subtag before '-' (e.g., `en-GB` -> `en`). Mapped to `:language` / `inLanguage` in outputs.

- `license` / `licence`
  - Expected: URL or CreativeWork describing a license.
  - TeSS: stored under `:licence` (note spelling) and resolved via `LicenceDictionary` when generating output; omitted if value is `'notspecified'`.

- `author`, `contributor`, `creator`
  - Expected: Person or Organization nodes.
  - TeSS: `extract_people` produces arrays of `{ name:, orcid: }` where ORCID is extracted by regex from `identifier` or `@id`. Generators emit `Person` objects and include `@id`/`identifier` when ORCID present.

- `provider` / `schema:provider`
  - Expected: Organization or URL.
  - TeSS: `extract_nodes` pulls provider names when present and maps them into `:node_names`. The `Generator.provider` builds an array of Organizations from `content_provider`, site `nodes`, and `host_institutions` for exports.

- `mentions`
  - Expected: referenced Things (tools, datasets). 
  - TeSS: `extract_mentions` returns `:external_resources` as an array of `{ title:, url: }`. Generator maps `external_resources` to `mentions` in output.

- `about`, `topic`, `subjectOf`
  - Expected: `DefinedTerm` (EDAM, ontologies) or plain text.
  - TeSS: `extract_topics` collects `:scientific_topic_names` and `:scientific_topic_uris` (EDAM URIs receive special capture). Generator converts internal term objects to `DefinedTerm` structures using `term.uri`, `term.ontology.uri`, and `term.label`.

- `location`, `PostalAddress`, `VirtualLocation`
  - Expected: `Place` with nested `PostalAddress`/`GeoCoordinates`, or `VirtualLocation`.
  - TeSS: `extract_location` merges Place, PostalAddress and VirtualLocation into keys: `:venue`, `:street_address`, `:city`, `:county`, `:country`, `:postcode`, `:latitude`, `:longitude`. If VirtualLocation present, it sets `:online` and appends the virtual name/url to the `venue` text. Generator uses `Generator.address(event)` to produce `Place`/`PostalAddress` when any location fields are present.

- `startDate`, `endDate`, `duration`
  - Expected: ISO 8601 date/time or duration.
  - TeSS: `:start`, `:end`, `:duration` keys. If `end` missing and `duration` present, the extractor computes `end` via `modify_date(start, duration)` (parses ISO 8601 durations PnYnMnDTnHnMnS partially supporting Y/M/W/D).

- `maximumAttendeeCapacity`
  - Expected: integer.
  - TeSS: extracted into `:capacity` and mapped to `maximumAttendeeCapacity` in generator output.

- `audience` / `EducationalAudience`
  - Expected: Audience or EducationalAudience with `audienceType`/`educationalRole` or simple string.
  - TeSS: `extract_audience` returns `:target_audience` array of strings. Generator builds `Audience` objects with `audienceType` when generating JSON-LD.

- `courseMode`
  - Expected: array of modes (e.g., `online`, `onsite`, `synchronous`).
  - TeSS: `extract_online` checks `courseMode` values for case-insensitive `online` and sets `:online` boolean in the extracted params. `CourseInstanceExtractor` sets `:event_types` to `[:workshops_and_courses]`.

- `teaches`, `competencyRequired`, `coursePrerequisites`, `learningObjectives`
  - Expected: `DefinedTerm` or text or AlignmentObject(s).
  - TeSS: `extract_names_or_values` and `extract_course_prerequisites` return strings or markdownified lists; `markdownify_list` returns a single string or a markdown bullet list (TeSS stores lists in markdown form and `Generator.markdown_to_array` reverses this when exporting). Generator maps these to `teaches`, `competencyRequired`, `coursePrerequisites`, etc.

---

## Type-specific notes

### LearningResource / TrainingMaterial
- Core properties TeSS uses: `name`, `learningResourceType`, `url`, `identifier` (DOI detection), `version`, `description`, `keywords`, `author`, `contributor`, `provider`, `audience`, `about`, `dateCreated`, `dateModified`, `datePublished`, `creativeWorkStatus`, `license`, `educationalLevel`, `competencyRequired`, `teaches`, `mentions`.
- Special handling:
  - `identifier` => DOI detection: extractor stores DOI in `:doi` when identifier matches DOI regex. Generator outputs `identifier` with DOI.
  - `licence` field spelled `licence` in extractor output; `Generator` resolves values via `LicenceDictionary` and outputs `license` in JSON-LD when appropriate.
  - `difficulty_level` uses `educationalLevel`; TeSS omits `educationalLevel` when difficulty_level == `'notspecified'`.

### Course
- TeSS merges Course-level metadata and CourseInstance metadata. `CourseExtractor` pulls `external_resources` (mentions) at the course level and overlays instance-specific fields.
- `hasCourseInstance` is output as an inlined CourseInstance (generator uses `Bioschemas::CourseInstanceGenerator.new(event).generate.except('@id')`).

### CourseInstance
- Core properties: `startDate`, `endDate`, `organizer`, `location`, `funder`, `maximumAttendeeCapacity`, `courseMode`.
- Special handling:
  - Virtual locations are merged into `venue` text and set `:online` true.
  - `courseMode` affects the `:online` boolean and `presence` mapping in generator.

### Event
- Core properties: `name`, `alternateName`, `description`, `keywords`, `startDate`, `endDate`, `organizer`, `location`, `hostInstitution`, `contact`, `funder`, `audience`, `maximumAttendeeCapacity`, `event_types`, `eligibility`, `about`.
- Special handling:
  - `organizer`: `extract_names_or_ids` returns strings `Name (URL)` when embedded nodes provide names with URIs; generator reduces to Organization objects when exporting.

---

## Non-official or helper properties processed by TeSS / extractors
- `http://schema.org/topic` — legacy topic property: extractor collects these into `:scientific_topic_names` if present.
- `http://schema.org/hostInstitution` — extractor recognizes and populates `:host_institutions`.
- `sioc:has_creator` — extractor checks `SIOC.has_creator` as alternate author source when building `:authors`.
- `http://schema.org/contact` — used as a fallback in `extract_contact` helper to gather contact name/email.
- `teaches` / `competencyRequired` — handled flexibly as DefinedTerm, Text, or AlignmentObject; extractor normalises to strings/markdown.

---

## Examples (extractor -> TeSS generator expectations)

- Extractor output keys mapped to generator properties (common):
  - `:title` -> generator `name`
  - `:description` -> `description`
  - `:url` -> `url`
  - `:keywords` -> `keywords`
  - `:authors` -> `author` (converted via `Generator.person`)
  - `:contributors` -> `contributor`
  - `:external_resources` -> `mentions`
  - `:scientific_topic_uris`/`:scientific_topic_names` -> `about` (DefinedTerm)

See `lib/bioschemas/*.rb` for generator-side mappings and `lib/tess/rdf/*_extractor.rb` in the `TeSS_RDF_Extractors` gem for extractor behaviours.

---

