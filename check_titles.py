from src.neo import Neo4jClient

neo = Neo4jClient()
result = neo.run('MATCH (p:Paper) RETURN p.title, p.source_path, p.paper_id ORDER BY p.title LIMIT 10')

print("Current paper titles in database:")
print("-" * 80)
for r in result:
    title = r.get("p.title", "NO TITLE")
    path = r.get("p.source_path", "NO PATH")
    print(f"Title: {title}")
    print(f"Path:  {path}")
    print("-" * 80)
