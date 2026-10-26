# Scope and evidence

Port of upstream2496 request-shape correction only. The real dashboard route and request serializer remain under test with synthetic private accounts and explicitly stubbed auth refresh, HTTP lease, and forced usage refresh. The old field produces400; removing it produces200. Reinserting it restores400. The positive proves fetched evidence can settle without writing a usage row. Existing failed-fetch settlement and cache invalidation tests remain. No real probe was sent.
