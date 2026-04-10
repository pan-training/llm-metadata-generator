# TrainingMaterial Profile

**Version:** 1.1-DRAFT (21 November 2022)
**Schema.org type:** `schema:LearningResource`
**Profile URL:** https://bioschemas.org/profiles/TrainingMaterial/1.1-DRAFT

## Description

A specification for describing training materials in life sciences. The Life Science Training Materials specification provides a way to describe bioscience training material on the World Wide Web. It defines a set of metadata and vocabularies, built on top of existing technologies and standards, that can be used to represent events in Web pages and applications. The goal of the specification is to make it easier to discover, exchange and integrate life science training material information across the Internet. Version: 1.1-DRAFT.

## Properties

CD = Cardinality (ONE or MANY).
Marginality levels: **Minimum** (required), **Recommended**, **Optional**.

### Minimum Properties

| Property | Expected Type | CD | Description | Controlled Vocabulary |
|---|---|---|---|---|
| `@context` | URL | ONE | Used to provide the context (namespaces) for the JSON-LD file. Not needed in other serialisations. |  |
| `@type` | Text | MANY | Schema.org/Bioschemas class for the resource declared using JSON-LD syntax. For other serialisations please use the appropriate mechanism. While it is permissible to provide multiple types, it is preferred to use a single type. | Schema.org, Bioschemas |
| `@id` | IRI | ONE | Used to distinguish the resource being described in JSON-LD. For other serialisations use the appropriate approach. |  |
| `dct:conformsTo` | IRI | ONE | Used to state the Bioschemas profile that the markup relates to. The versioned URL of the profile must be used. Note that we use a CURIE in the table here but the full URL for Dublin Core terms must be used in the markup (http://purl.org/dc/terms/conformsTo), see example. | Bioschemas profile versioned URL |
| `description` | Text | ONE | A description of the item. |  |
| `keywords` | DefinedTerm / Text / URL | MANY | Keywords or tags used to describe this content. Multiple entries in a keywords list are typically delimited by commas. |  |
| `name` | Text | ONE | The name of the item. |  |

### Recommended Properties

| Property | Expected Type | CD | Description | Controlled Vocabulary |
|---|---|---|---|---|
| `about` | Thing | MANY | The subject of this Training Material. Use the DefinedTerm type to add a controlled vocabulary term to describe the topic (such as from the EDAM ontology) The subject matter of the content. Inverse property: subjectOf. |  |
| `abstract` | Text | ONE | An abstract is a short description that summarizes a CreativeWork. |  |
| `audience` | Audience | MANY | A succinct description of the intended target audience for your materials: e.g., graduates, postgraduates, clinicians. An intended audience, i.e. a group for whom something was created. Supersedes serviceAudience. |  |
| `author` | Organization / Person | MANY | Those involved in the preparation, creation and/or presentation of the published work, specifically writing the initial draft The author of this content or rating. Please note that author is special in that HTML 5 provides a special mechanism for indicating authorship via the rel tag. That is equivalent to this and may be used interchangeably. |  |
| `competencyRequired` | DefinedTerm / Text / URL | MANY | Knowledge, skill, ability or personal attribute that must be demonstrated by a person or other entity in order to do something such as earn an Educational Occupational Credential or understand a LearningResource. |  |
| `educationalLevel` | DefinedTerm / Text / URL | ONE | The students level of ability in the topic being taught. Examples of skill levels include ‘beginner’, ‘intermediate’ or ‘advanced’. The level in terms of progression through an educational or training context. Examples of educational levels include ‘beginner’, ‘intermediate’ or ‘advanced’, and formal sets of level indicators. |  |
| `identifier` | PropertyValue / Text / URL | MANY | An identifier for this resource such as a DOI or compact URI The identifier property represents any kind of identifier for any kind of Thing, such as ISBNs, GTIN codes, UUIDs etc. Schema.org provides dedicated properties for representing many of these, either as textual strings or as URL (URI) links. See background notes for more details. |  |
| `inLanguage` | Language / Text | MANY | Defaults to English if not specified. Please choose a value from IETF BCP 47 standard . You can add multiple languages if the Training Material offers different translations The language of the content or performance or used in an action. Please use one of the language codes from the IETF BCP 47 standard. See also availableLanguage. Supersedes language. |  |
| `learningResourceType` | DefinedTerm / Text | MANY | This may include things such as video lecture, e-Learning module, or tutorial. The predominant type or kind characterizing the learning resource. For example, ‘presentation’, ‘handout’. |  |
| `license` | CreativeWork / URL | MANY | If there is a licence it must be added. A license document that applies to this content, typically indicated by URL. |  |
| `mentions` | Thing | MANY | Datasets, tools, technologies, entities etc, which are referred to by this training material or actively used in this training material. Indicates that the CreativeWork contains a reference to, but is not necessarily about a concept. |  |
| `sameAs` | URL | MANY | URL of a reference Web page that unambiguously indicates the item’s identity. E.g. the URL of the item’s Wikipedia page, Wikidata entry, or official website. |  |
| `teaches` | DefinedTerm / Text | MANY | The item being described is intended to help a person learn the competency or learning outcome defined by the referenced term. |  |
| `timeRequired` | Duration | ONE | The estimated time it takes to work through this resource. Please specify in ISO 8601 duration format . Approximate or typical time it takes to work with or through this learning resource for the typical intended target audience, e.g. ‘PT30M’, ‘PT1H25M’. |  |
| `url` | URL | ONE | The preferred URL of the Training Material. You must provide this value if it is known. URL of the item. |  |

### Optional Properties

| Property | Expected Type | CD | Description | Controlled Vocabulary |
|---|---|---|---|---|
| `accessibilitySummary` | Text | ONE | A human-readable summary of specific accessibility features or deficiencies, consistent with the other accessibility metadata but expressing subtleties such as “short descriptions are present but long descriptions will be needed for non-visual users” or “short descriptions are present and no long descriptions are needed.” |  |
| `contributor` | Organization / Person | MANY | Contributors are those that made non-authorship contributions e.g. critical review, commentary or revision A secondary contributor to the CreativeWork or Event. |  |
| `creativeWorkStatus` | DefinedTerm / Text | ONE | The status of a training material. If this is not filled in it will be regarded as Active. Options are ‘Active’, ‘Under development’, and ‘Archived’. The status of a creative work in terms of its stage in a lifecycle. Example terms include Incomplete, Draft, Published, Obsolete. Some organizations define a set of terms for the stages of their publication lifecycle. |  |
| `dateCreated` | Date / DateTime | ONE | The date on which the CreativeWork was created or the item was added to a DataFeed. |  |
| `dateModified` | Date / DateTime | ONE | The date on which the CreativeWork was most recently modified or when the item’s entry was modified within a DataFeed. |  |
| `datePublished` | Date / DateTime | ONE | Date of first broadcast/publication. |  |
| `hasPart` | CreativeWork | MANY | A sub-training material or externally referenced training material Indicates an item or CreativeWork that is part of this item, or CreativeWork (in some sense). Inverse property: isPartOf. |  |
| `isPartOf` | CreativeWork / URL | MANY | The Course this Training Material was/will be used in. Or a training material this training material is a part of (for example, if this is a module in a book, isPartOf can describe the book). Inverse property: hasPart If this varies in CourseInstances, use the workFeatured property Indicates an item or CreativeWork that this item, or CreativeWork (in some sense), is part of. Inverse property: hasPart. |  |
| `recordedAt` | Event | MANY | The course instance or event where this training material was or will be featured. Use isPartOf to refer to a Course, unless this training material is unique to a specific Course Instance. The Event where the CreativeWork was recorded. The CreativeWork may capture all or part of the event. Inverse property: recordedIn. |  |
| `version` | Number / Text | ONE | If this training material is versioned, its strongly recommended you use this property to list the version being displayed The version of the CreativeWork embodied by a specified resource. |  |
| `workTranslation` | CreativeWork | MANY | A work that is a translation of the content of this work. e.g. 西遊記 has an English workTranslation “Journey to the West”,a German workTranslation “Monkeys Pilgerfahrt” and a Vietnamese translation Tây du ký bình khảo. Inverse property: translationOfWork. |  |
