# Course Profile

**Version:** 1.0-RELEASE (13 September 2022)
**Schema.org type:** `schema:Course`
**Profile URL:** https://bioschemas.org/profiles/Course/1.0-RELEASE

## Description

This specification must be used in tandem with a CourseInstance. A course is used to describe the broad, common aspects of a recurring training event - whereas a course instance is about the specific times and location of when that course is held.

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
| `keywords` | DefinedTerm / Text / URL | ONE | Keywords or tags used to describe this content. Multiple entries in a keywords list are typically delimited by commas. Bioschemas: Free text keywords delimited by commas describing the Course content. |  |
| `name` | Text | ONE | The name of the item. |  |

### Recommended Properties

| Property | Expected Type | CD | Description | Controlled Vocabulary |
|---|---|---|---|---|
| `about` | Thing | MANY | The subject matter of the content. Inverse property: subjectOf. Bioschemas: The subject of this Course. Use the DefinedTerm type to add a controlled vocabulary term to categorise the course (such as using the EDAM Topic ontology ). | EDAM term |
| `abstract` | Text | ONE | An abstract is a short description that summarizes a CreativeWork Bioschemas: An abstract is a short description that summarizes a Course |  |
| `aggregateRating` | AggregateRating | ONE | The overall rating, based on a collection of reviews or ratings, of the item. |  |
| `citation` | CreativeWork / Text | MANY | A citation or reference to another creative work, such as another publication, web page, scholarly article, etc. |  |
| `coursePrerequisites` | AlignmentObject / Course / Text | MANY | Requirements for taking the Course. May be completion of another Course or a textual description like “permission of instructor”. Requirements may be a pre-requisite competency, referenced using AlignmentObject. |  |
| `educationalLevel` | DefinedTerm / Text / URL | ONE | The level in terms of progression through an educational or training context. Examples of educational levels include ‘beginner’, ‘intermediate’ or ‘advanced’, and formal sets of level indicators. Bioschemas: The level expected to be able to participate in the course. Examples of educational levels include ‘beginner’, ‘intermediate’ or ‘advanced’. | Beginner, Intermediate, Advanced |
| `hasCourseInstance` | CourseInstance | MANY | An offering of the course at a specific time and place or through specific media or mode of study or to a specific section of students. Bioschemas: A course may be ran multiple times in different locations or at different times. Use hasCourseInstance to list the offerings of this Course. Please see the Course Instance specification for the full list of properties. |  |
| `license` | CreativeWork / URL | ONE | A license document that applies to this content, typically indicated by URL. Bioschemas: If the Course has an open-source license, include the short URL, as found in OSI. Otherwise use CreativeWork to describe your custom license. |  |
| `mentions` | Thing | MANY | Indicates that the CreativeWork contains a reference to, but is not necessarily about a concept. Bioschemas: Datasets, tools, technologies, entities etc, which are actively used by and/or referred to by this course. |  |
| `provider` | Organization / Person | MANY | The service provider, service operator, or service performer; the goods producer. Another party (a seller) may offer those services or goods on behalf of the provider. A provider may also serve as the seller. Supersedes carrier. Bioschemas: The organization responsible for providing the educational input for the course, e.g. content, assessments, accreditation etc. Note: providing a course goes beyond creating it as it implies some degree of academic responsibility for accrediting the content of the course, perhaps running assessments etc. |  |
| `teaches` | DefinedTerm / Text | MANY | The item being described is intended to help a person learn the competency or learning outcome defined by the referenced term. Bioschemas: Statements that describe what knowledge, skills or abilities students should acquire on completion of this Course | It is recommended that you utilize terms from the Blooms taxonomy |
| `timeRequired` | Duration | ONE | Approximate or typical time it takes to work with or through this learning resource for the typical intended target audience, e.g. ‘P30M’, ‘P1H25M’. Bioschemas: Approximate or typical time it takes to work through this learning resource for the typical intended target audience, e.g. ‘P30M’, ‘P1H25M’. This should use the ISO 8601 duration format. If this varies in a CourseInstance , use duration in CourseInstance to override timeRequired. | ISO 8601 |
| `url` | URL | ONE | URL of the item. Bioschemas: The preferred URL of the course page. You must provide this value if it is known. |  |

### Optional Properties

| Property | Expected Type | CD | Description | Controlled Vocabulary |
|---|---|---|---|---|
| `accessibilitySummary` | Text | ONE | A human-readable summary of specific accessibility features or deficiencies, consistent with the other accessibility metadata but expressing subtleties such as “short descriptions are present but long descriptions will be needed for non-visual users” or “short descriptions are present and no long descriptions are needed.” Bioschemas: A human-readable summary of specific accessibility features or deficiencies within the course. |  |
| `alternateName` | Text | MANY | An alias for the item. |  |
| `audience` | Audience | MANY | An intended audience, i.e. a group for whom something was created. Supersedes serviceAudience. Bioschemas: The type of audience intended for your course. A succinct description of the attendees. |  |
| `comment` | Comment | MANY | Comments, typically from users. |  |
| `commentCount` | Integer | ONE | The number of comments this CreativeWork (e.g. Article, Question or Answer) has received. This is most applicable to works published in Web sites with commenting system; additional comments may exist elsewhere. |  |
| `courseCode` | Text | MANY | The identifier for the Course used by the course provider (e.g. CS101 or 6.001). |  |
| `creator` | Organization / Person | MANY | The creator/author of this CreativeWork. This is the same as the Author property for CreativeWork. Bioschemas: The creator/author of the course. Note, this may be different from the instructor who delivers it (descibed in CourseInstance), or the author who created the training materials used. |  |
| `dateCreated` | Date / DateTime | ONE | The date on which the CreativeWork was created or the item was added to a DataFeed. |  |
| `dateModified` | Date / DateTime | MANY | The date on which the CreativeWork was most recently modified or when the item’s entry was modified within a DataFeed. |  |
| `educationalCredentialAwarded` | EducationalOccupationalCredential / Text / URL | ONE | A description of the qualification, award, certificate, diploma or other educational credential awarded as a consequence of successful completion of this course. Bioschemas: Strongly recommended if exists. A description of the qualification, award, certificate, diploma or other educational credential awarded as a consequence of successful completion of this course. |  |
| `hasPart` | CreativeWork | MANY | Indicates an item or CreativeWork that is part of this item, or CreativeWork (in some sense). Inverse property: isPartOf. Bioschemas: A training material used in a course such as an exercise, handouts, or slides. Inverse property: isPartOf. If this varies in a CourseInstance, use workFeatutred to override this property value. |  |
| `image` | ImageObject / URL | ONE | An image of the item. This can be a URL or a fully described ImageObject. |  |
| `isBasedOn` | CreativeWork / Product / URL | MANY | A resource that was used in the creation of this resource. This term can be repeated for multiple sources. For example, http://example.com/great-multiplication-intro.html. Supersedes isBasedOnUrl. Bioschemas: A course, book or other resource on which this Course is based. |  |
| `thumbnailUrl` | URL | ONE | A thumbnail image relevant to the Thing. |  |
