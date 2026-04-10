# Bioschemas — Examples for TeSS ingestion

Short JSON-LD snippets demonstrating how properties may be presented in Bioschemas/Schema.org markup and how TeSS extractors/ingestor will interpret them. Each example has a one-line note.

---

<!-- 1. Minimal LearningResource with DOI and structured authors -->
```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/rl1",
  "url": "https://example.org/materials/rl1",
  "name": "Introduction to Bioinformatics",
  "description": "A short guide to basic bioinformatics concepts.",
  "identifier": "https://doi.org/10.1234/example.doi",
  "author": [
    { "@type": "Person", "name": "Dr Alice Example", "identifier": "https://orcid.org/0000-0001-2345-6789" },
    { "@type": "Person", "name": "Bob Researcher" }
  ],
  "license": "https://creativecommons.org/licenses/by/4.0/",
  "keywords": "bioinformatics, sequencing, tutorial"
}
```

<!-- 2. TrainingMaterial with HTML description (ingestor converts to Markdown) and explicit dct:conformsTo -->
```json
{
  "@context": {
    "@vocab": "https://schema.org/",
    "dct": "http://purl.org/dc/terms/"
  },
  "@type": "TrainingMaterial",
  "@id": "https://example.org/materials/tm-html",
  "url": "https://example.org/materials/tm-html",
  "name": "Hands-on RNA-Seq Workshop Materials",
  "description": "<p>This <strong>workshop</strong> includes slides, examples and datasets.</p>\n<ul><li>Slides</li><li>Data</li></ul>",
  "dct:conformsTo": "https://bioschemas.org/profiles/TrainingMaterial/1.0-RELEASE",
  "author": { "@type": "Person", "name": "Claire Tutor" },
  "mentions": [ { "@type": "CreativeWork", "name": "Sample dataset", "url": "https://data.example.org/ds1" } ],
  "keywords": ["RNA-seq","workshop"],
  "inLanguage": "en-GB"
}
```

<!-- 3. Course with DefinedTerm topics and nested CourseInstance in `hasCourseInstance` -->
```json
{
  "@context": "https://schema.org/",
  "@type": "Course",
  "@id": "https://example.org/courses/c1",
  "name": "Applied Genomics",
  "description": "A semester-long module on applied genomics.",
  "about": [
    { "@type": "DefinedTerm", "name": "Genome assembly", "url": "http://edamontology.org/topic_1234" }
  ],
  "hasCourseInstance": {
    "@type": "CourseInstance",
    "@id": "https://example.org/courses/c1/inst1",
    "name": "Applied Genomics — 2026 cohort",
    "startDate": "2026-09-01",
    "endDate": "2026-12-15",
    "location": {
      "@type": "Place",
      "name": "University Campus",
      "address": {
        "@type": "PostalAddress",
        "addressLocality": "Springfield",
        "addressCountry": "GB"
      }
    }
  }
}
```

<!-- 4. CourseInstance/Event with start + duration (no end) — extractor may compute `end` -->
```json
{
  "@context": "https://schema.org/",
  "@type": "CourseInstance",
  "@id": "https://example.org/events/e-duration",
  "name": "Two-day Data Carpentry Workshop",
  "startDate": "2026-05-10T09:00:00Z",
  "duration": "P2D",
  "maximumAttendeeCapacity": 30,
  "location": {
    "@type": "Place",
    "name": "Research Institute",
    "geo": { "@type": "GeoCoordinates", "latitude": "52.1", "longitude": "-1.2" }
  },
  "organizer": { "@type": "Organization", "name": "BioTraining Ltd" }
}
```

<!-- 5. Virtual event using VirtualLocation and courseMode indicating online -->
```json
{
  "@context": "https://schema.org/",
  "@type": "Event",
  "@id": "https://example.org/events/virtual1",
  "name": "Webinar: Reproducible Research",
  "startDate": "2026-06-20T14:00:00Z",
  "endDate": "2026-06-20T15:30:00Z",
  "location": {
    "@type": "VirtualLocation",
    "name": "Zoom meeting",
    "url": "https://zoom.example/meet/12345"
  },
  "courseMode": ["online", "synchronous"],
  "audience": { "@type": "Audience", "audienceType": "Researchers" }
}
```

<!-- 6. Material with authors as plain strings and `identifier` as non-DOI (shows extractor behaviour difference) -->
```json
{
  "@context": "https://schema.org/",
  "@type": "CreativeWork",
  "@id": "https://example.org/materials/plain-authors",
  "url": "https://example.org/materials/plain-authors",
  "name": "Dataset documentation",
  "description": "Documentation for dataset XYZ.",
  "author": ["Data Curator One", "Data Curator Two"],
  "identifier": "dataset-xyz-v1",
  "license": "CC-BY-4.0"
}
```

<!-- 7. LearningResource showing multiple languages and target audience, plus external_resources/mentions -->
```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/multi-lang",
  "name": "Genomics Quick Reference",
  "description": "Quick reference guide.",
  "inLanguage": ["en", "es"],
  "targetAudience": ["Undergraduate students", "Bioinformaticians"],
  "mentions": [ { "@type": "WebPage", "name": "Reference appendix", "url": "https://example.org/appendix" } ]
}
```

<!-- 8. Onsite event / workshop with explicit `courseMode: ["onsite"]` and PostalAddress fields -->
```json
{
  "@context": "https://schema.org/",
  "@type": "Event",
  "@id": "https://example.org/events/onsite1",
  "name": "In-person Genomics Workshop",
  "startDate": "2026-07-12T09:30:00Z",
  "endDate": "2026-07-12T17:00:00Z",
  "courseMode": ["onsite"],
  "maximumAttendeeCapacity": 25,
  "location": {
    "@type": "Place",
    "name": "Main Lecture Hall",
    "address": {
      "@type": "PostalAddress",
      "streetAddress": "1 Science Road",
      "addressLocality": "Exampletown",
      "addressRegion": "Exshire",
      "postalCode": "EX1 2PL",
      "addressCountry": "GB"
    }
  },
  "audience": "Life scientists",
  "organizer": { "@type": "Organization", "name": "Example Training" }
}
```

<!-- 9. Identifier as PropertyValue (DOI encoded as PropertyValue) -->
```json
{
  "@context": "https://schema.org/",
  "@type": "CreativeWork",
  "@id": "https://example.org/materials/pv-id",
  "name": "Analysis Cookbook",
  "identifier": {
    "@type": "PropertyValue",
    "propertyID": "doi",
    "value": "10.5678/example.cookbook"
  },
  "author": { "@type": "Person", "@id": "https://orcid.org/0000-0002-3456-7890", "name": "Dr ORCID Author" }
}
```

<!-- 10. Licence expressed as textual signal 'notspecified' and as SPDX string; TeSS treats 'notspecified' specially -->
```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/licence-variants",
  "name": "Licence variants example",
  "license": "notspecified",
  "keywords": "tutorial, rna-seq",
  "author": "Anon"
}
```

```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/licence-spdx",
  "name": "Licence SPDX example",
  "license": "CC-BY-4.0",
  "author": ["Curator A"]
}
```

<!-- 11. Authors with ORCID in `@id` and contributor as Organization -->
```json
{
  "@context": "https://schema.org/",
  "@type": "CreativeWork",
  "@id": "https://example.org/materials/orcid-id",
  "name": "Paper with ORCID author",
  "author": [ { "@type": "Person", "@id": "https://orcid.org/0000-0002-9999-8888", "name": "Ella Researcher" } ],
  "contributor": { "@type": "Organization", "name": "Research Org" }
}
```

<!-- 12. Provider as URL instead of object, and `hostInstitution` custom usage -->
```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/provider-url",
  "name": "Node-provided material",
  "provider": "https://node.example.org",
  "hostInstitution": "Example University"
}
```

<!-- 13. Prerequisites as array (markdownified by extractor) and as AlignmentObject -->
```json
{
  "@context": "https://schema.org/",
  "@type": "Course",
  "@id": "https://example.org/courses/prereq",
  "name": "Advanced Analysis",
  "coursePrerequisites": ["Basic Unix skills","Intro to R"],
  "competencyRequired": {
    "@type": "AlignmentObject",
    "alignmentType": "requires",
    "targetName": "Intro to Statistics"
  }
}
```

<!-- 14. Course with multiple CourseInstances (array) including virtual and onsite -->
```json
{
  "@context": "https://schema.org/",
  "@type": "Course",
  "@id": "https://example.org/courses/multi-inst",
  "name": "Data Skills Series",
  "hasCourseInstance": [
    {
      "@type": "CourseInstance",
      "@id": "https://example.org/courses/multi-inst/inst1",
      "name": "Data Skills — Virtual",
      "startDate": "2026-08-01T09:00:00Z",
      "endDate": "2026-08-01T12:00:00Z",
      "location": { "@type": "VirtualLocation", "name": "Zoom", "url": "https://zoom.example/meet/abc" },
      "courseMode": ["online"]
    },
    {
      "@type": "CourseInstance",
      "@id": "https://example.org/courses/multi-inst/inst2",
      "name": "Data Skills — In person",
      "startDate": "2026-09-10T09:00:00Z",
      "endDate": "2026-09-10T17:00:00Z",
      "location": { "@type": "Place", "name": "Campus Room" },
      "courseMode": ["onsite"]
    }
  ]
}
```

<!-- 15. Resource with version, dateCreated, dateModified, datePublished and resource type -->
```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/fullmeta",
  "name": "Comprehensive Guide",
  "version": "2.1",
  "dateCreated": "2024-11-01",
  "dateModified": "2026-01-15",
  "datePublished": "2025-02-20",
  "learningResourceType": ["Tutorial","Reference"],
  "difficulty_level": "intermediate"
}
```

<!-- 16. Mentions / external_resources as list of objects with titles and urls -->
```json
{
  "@context": "https://schema.org/",
  "@type": "CreativeWork",
  "@id": "https://example.org/materials/with-mentions",
  "name": "Resource with mentions",
  "mentions": [
    { "@type": "WebPage", "name": "Supplement", "url": "https://example.org/supp" },
    { "@type": "Dataset", "name": "Sample data", "url": "https://data.example.org/sample" }
  ]
}
```

<!-- 17. `teaches` as DefinedTerm (with ontology URI) and as plain text array -->
```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/teaches-definedterm",
  "name": "Assembly Techniques Guide",
  "teaches": [
    { "@type": "DefinedTerm", "name": "Genome assembly", "url": "http://edamontology.org/topic_1234" }
  ],
  "learningObjectives": ["Understand de novo assembly","Use SPAdes for bacterial genomes"]
}
```

```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/teaches-strings",
  "name": "Practical Assembly Exercises",
  "teaches": ["de novo assembly","error correction"],
  "learningObjectives": "Practice running assemblers on small genomes"
}
```

<!-- 18. `competencyRequired` as simple string and as AlignmentObject (both shown) -->
```json
{
  "@context": "https://schema.org/",
  "@type": "Course",
  "@id": "https://example.org/courses/comp-string",
  "name": "Introductory Workshop",
  "competencyRequired": "Basic Unix skills"
}
```

```json
{
  "@context": "https://schema.org/",
  "@type": "Course",
  "@id": "https://example.org/courses/comp-align",
  "name": "Statistics for Bioinformatics",
  "competencyRequired": {
    "@type": "AlignmentObject",
    "alignmentType": "requires",
    "targetUrl": "http://example.org/competency/stats-101",
    "targetName": "Intro to Statistics"
  }
}
```

<!-- 19. `about` as simple text, as URI, and as DefinedTerm array -->
```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/about-text",
  "name": "Intro to Sequencing",
  "about": "Next-generation sequencing"
}
```

```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/about-uri",
  "name": "EDAM-linked resource",
  "about": "http://edamontology.org/topic_0922"
}
```

```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/about-defined",
  "name": "Topic-tagged guide",
  "about": [ { "@type": "DefinedTerm", "name": "Sequence alignment", "url": "http://edamontology.org/topic_0674" } ]
}
```

<!-- 20. `learningResourceType` as single string and array, and `resourceType` variants -->
```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/resourcetype-single",
  "name": "Quick Tutorial",
  "learningResourceType": "Tutorial"
}
```

```json
{
  "@context": "https://schema.org/",
  "@type": "LearningResource",
  "@id": "https://example.org/materials/resourcetype-array",
  "name": "Combined Guide",
  "learningResourceType": ["Tutorial","Reference"]
}
```

---
---

Notes for authors: these examples are intentionally small; real pages may mix the patterns illustrated above (e.g., structured authors and comma-separated `keywords`). For TeSS ingestion, prefer providing `url`, `name`, `description`, and `identifier` (DOI) where available — include `dct:conformsTo` to explicitly mark Bioschemas profiles. The set above includes examples showing:

- `identifier` as DOI URL, plain string, and `PropertyValue` with `propertyID`/`value`;
- `author` as structured Person (with `identifier`/ORCID or `@id`), and as simple strings;
- `license` as URL, SPDX short string, and the special `notspecified` sentinel;
- `location` as `Place`+`PostalAddress` and as `VirtualLocation` and `GeoCoordinates`;
- `courseMode` values `online` and `onsite` (both in examples);
- `teaches` as `DefinedTerm` (URI) and as plain string arrays;
- `competencyRequired` as string and `AlignmentObject`;
- `coursePrerequisites` as array of strings and as `AlignmentObject`;
- `learningObjectives` as array of strings and as single string;
- `about` as plain text, URI, and `DefinedTerm`.
