// ---------- Constraints ----------
CREATE CONSTRAINT paper_id IF NOT EXISTS
FOR (p:Paper) REQUIRE p.paper_id IS UNIQUE;

CREATE CONSTRAINT section_id IF NOT EXISTS
FOR (s:Section) REQUIRE s.section_id IS UNIQUE;

CREATE CONSTRAINT paragraph_id IF NOT EXISTS
FOR (p:Paragraph) REQUIRE p.paragraph_id IS UNIQUE;

CREATE CONSTRAINT figure_id IF NOT EXISTS
FOR (f:Figure) REQUIRE f.figure_id IS UNIQUE;

CREATE CONSTRAINT umbrella_id IF NOT EXISTS
FOR (u:Umbrella) REQUIRE u.umbrella_id IS UNIQUE;

// ---------- Vector Indexes (3072-D, text-embedding-3-large) ----------
CREATE VECTOR INDEX paragraph_embedding_index IF NOT EXISTS
FOR (p:Paragraph) ON (p.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 3072,
  `vector.similarity_function`: 'cosine'
}};

CREATE VECTOR INDEX figure_embedding_index IF NOT EXISTS
FOR (f:Figure) ON (f.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 3072,
  `vector.similarity_function`: 'cosine'
}};

// ---------- Concepts & Topics ----------
CREATE CONSTRAINT concept_id IF NOT EXISTS
FOR (c:Concept) REQUIRE c.concept_id IS UNIQUE;

CREATE CONSTRAINT topic_name IF NOT EXISTS
FOR (t:Topic) REQUIRE t.name IS UNIQUE;

CREATE VECTOR INDEX concept_embedding_index IF NOT EXISTS
FOR (c:Concept) ON (c.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 3072,
  `vector.similarity_function`: 'cosine'
}};

// ---------- Semantic Relations ----------
// Indexes for relation properties to enable efficient querying
CREATE INDEX relation_type_index IF NOT EXISTS
FOR ()-[r:SEMANTIC_RELATION]-() ON (r.relation_type);

CREATE INDEX relation_confidence_index IF NOT EXISTS
FOR ()-[r:SEMANTIC_RELATION]-() ON (r.confidence);

// Index for co-occurrence relations
CREATE INDEX cooccurrence_strength_index IF NOT EXISTS
FOR ()-[r:CO_OCCURS_WITH]-() ON (r.strength);