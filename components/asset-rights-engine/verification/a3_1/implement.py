from pathlib import Path
p=Path('src/asset_rights_engine/engine.py')
s=p.read_text()
s=s.replace('import sqlite3, json, hashlib','import sqlite3, json, hashlib, re')
start=s.index('            else:\n                require(c.execute("SELECT checksum')
end=s.index('    def register_asset',start)
s=s[:start]+'''            self._validate_database(c, schema)

    @staticmethod
    def _sql_tokens(sql):
        """Compare supported schema definitions, ignoring cosmetic SQL spelling."""
        tokens=re.findall(r"--[^\\n]*|/\\*[\\s\\S]*?\\*/|'(?:''|[^'])*'|\\\"(?:\\\"\\\"|[^\\\"])*\\\"|`[^`]*`|\\[[^]]*\\]|[A-Za-z_][A-Za-z_0-9]*|[^\\s]",sql or "")
        result=[]
        for token in tokens:
            if token.startswith(("--","/*")) or token==";": continue
            if token.startswith("'"): result.append(token)
            elif token.startswith(('"','`','[')): result.append(token[1:-1].lower())
            else: result.append(token.lower())
        if result[:5]==["create","table","if","not","exists"]: del result[2:5]
        return result

    @staticmethod
    def _stored(context, operation):
        try: return operation()
        except DomainError as e:
            raise DomainError("VALIDATION",f"{context}: {e.message}") from e
        except (ValueError,TypeError,KeyError,AttributeError,RecursionError) as e:
            # Do not expose payload text or turn unrelated programming errors into success.
            raise DomainError("VALIDATION",f"{context}: invalid persisted content ({type(e).__name__})") from e

    @classmethod
    def _payload(cls, raw, context):
        def parse():
            def unique(pairs):
                obj={}
                for key,value in pairs:
                    require(key not in obj,"Duplicate JSON property")
                    obj[key]=value
                return obj
            def constant(value): raise ValueError("Non-finite JSON number")
            value=json.loads(raw,object_pairs_hook=unique,parse_constant=constant)
            require(isinstance(value,dict),"Expected JSON object")
            return value
        return cls._stored(context,parse)

    def _validate_database(self,c,schema):
        require(c.execute("PRAGMA user_version").fetchone()[0]==1,"Unsupported schema")
        require(c.execute("PRAGMA integrity_check").fetchone()[0]=="ok","Integrity check failed")
        # The package schema is the schema-v1 authority, not an editable ledger checksum.
        # Compare all required tables/columns/constraints, while allowing extra objects.
        with sqlite3.connect(":memory:") as expected:
            expected.executescript(schema)
            for name,sql in expected.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence'"):
                actual=c.execute("SELECT type,sql FROM sqlite_master WHERE name=?",(name,)).fetchone()
                require(actual is not None and actual["type"]=="table",f"Missing required table: {name}")
                wanted=list(expected.execute(f'PRAGMA table_xinfo("{name}")'))
                found=[tuple(row) for row in c.execute(f'PRAGMA table_xinfo("{name}")')]
                require(found==wanted,f"Required columns differ: {name}")
                require(self._sql_tokens(actual["sql"])==self._sql_tokens(sql),f"Required schema definition differs: {name}")
        for table in ("assets","rights_revisions","assignment_revisions","review_history","migrations"):
            for action in ("update","delete"):
                name=f"immutable_{table}_{action}"
                row=c.execute("SELECT tbl_name,sql FROM sqlite_master WHERE type='trigger' AND name=?",(name,)).fetchone()
                require(row is not None,f"Missing immutability trigger: {name}")
                expected=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable record'); END"
                require(row["tbl_name"]==table and self._sql_tokens(row["sql"])==self._sql_tokens(expected),f"Invalid immutability trigger definition: {name}")
        ledger=c.execute("SELECT version,applied_at,checksum FROM migrations").fetchall()
        require(len(ledger)==1 and type(ledger[0]["version"]) is int and ledger[0]["version"]==1,"Migration ledger must contain exactly schema version 1")
        self._stored("migrations version 1",lambda: instant(ledger[0]["applied_at"]))
        self._stored("migrations version 1",lambda: sha(ledger[0]["checksum"]))
        require(ledger[0]["checksum"]==hashlib.sha256(schema.encode()).hexdigest(),"Schema migration checksum mismatch")
        require(not c.execute("PRAGMA foreign_key_check").fetchall(),"Foreign key violations")
        self._validate_contents(c)
        return {"valid":True,"schema_version":1}

    def _validate_contents(self,c):
        for row in c.execute("SELECT * FROM assets"):
            context=f"assets {row['asset_id']}"
            payload=self._payload(row["payload"],context)
            asset=self._stored(context,lambda: AssetDescriptor(**payload))
            require(asset.asset_id==row["asset_id"],f"{context}: Asset identity mismatch")
        for row in c.execute("SELECT * FROM assignments"):
            self._stored("assignments identity",lambda: identifier(row["assignment_id"]))
            require(c.execute("SELECT 1 FROM assignment_revisions WHERE entity_id=?",(row["assignment_id"],)).fetchone() is not None,"Assignment has no revisions")
        for table in ("rights_revisions","assignment_revisions"):
            for row in c.execute(f"SELECT * FROM {table}"):
                context=f"{table} {row['entity_id']} revision {row['revision']}"
                payload=self._payload(row["payload"],context)
                require(digest(payload)==row["digest"],f"{context}: Content digest mismatch")
                if table=="rights_revisions":
                    record=self._stored(context,lambda: RightsRecord(**payload))
                    require(record.state!=RightsState.APPROVED,f"{context}: Approval requires review history")
                else:
                    def assignment():
                        values=dict(payload)
                        values["binding"]=SceneBinding(**values["binding"])
                        values["usage"]=UseContext(**values["usage"])
                        return AssignmentDescriptor(**values)
                    record=self._stored(context,assignment)
                    require(record.state in (AssignmentState.DRAFT,AssignmentState.PENDING_REVIEW,AssignmentState.BLOCKED),f"{context}: Invalid persisted assignment state")
                    asset=c.execute("SELECT asset_id FROM assignments WHERE assignment_id=?",(row["entity_id"],)).fetchone()
                    require(asset is not None and asset[0]==record.asset_id,f"{context}: Assignment asset mismatch")
                    rights=c.execute("SELECT digest FROM rights_revisions WHERE entity_id=? AND revision=?",(record.asset_id,record.rights_revision)).fetchone()
                    require(rights is not None and rights[0]==record.rights_hash,f"{context}: Rights reference mismatch")
        histories={}
        for row in c.execute("SELECT * FROM review_history ORDER BY sequence"):
            context=f"review_history sequence {row['sequence']}"
            payload=self._payload(row["request_payload"],context)
            request=self._stored(context,lambda: ReviewRequest(**payload["request"]))
            require(set(payload)=={"kind","entity_id","request","binding","context"},f"{context}: Review payload fields differ")
            require(row["kind"] in ("rights","assignment") and payload["kind"]==row["kind"] and payload["entity_id"]==row["entity_id"],f"{context}: Review identity mismatch")
            fields=("reviewer","decision","note","content_digest","reviewed_at","idempotency_key")
            require(all(row[key]==getattr(request,key) for key in fields) and row["revision"]==request.expected_revision,f"{context}: Review request mismatch")
            table="rights_revisions" if row["kind"]=="rights" else "assignment_revisions"
            revision_row=c.execute(f"SELECT digest FROM {table} WHERE entity_id=? AND revision=?",(row["entity_id"],row["revision"])).fetchone()
            require(revision_row is not None and revision_row[0]==request.content_digest,f"{context}: Reviewed revision mismatch")
            if payload["binding"] is not None: self._stored(context,lambda: SceneBinding(**payload["binding"]))
            if payload["context"] is not None: self._stored(context,lambda: UseContext(**payload["context"]))
            require(row["kind"]!="rights" or (payload["binding"] is None and payload["context"] is None),f"{context}: Unexpected rights review context")
            key=(row["kind"],row["entity_id"],row["revision"])
            require(key not in histories,f"{context}: Duplicate revision review")
            histories[key]=row["decision"]
        states={}
        for row in c.execute("SELECT * FROM review_state"):
            key=(row["kind"],row["entity_id"],row["revision"])
            require(key in histories and histories[key]==row["decision"],"review_state: Missing or inconsistent review history")
            table="rights_revisions" if row["kind"]=="rights" else "assignment_revisions"
            latest=c.execute(f"SELECT MAX(revision) FROM {table} WHERE entity_id=?",(row["entity_id"],)).fetchone()[0]
            require(row["revision"]==latest,"review_state: Stale reviewed revision")
            states[key]=row["decision"]
        for key,decision in histories.items():
            table="rights_revisions" if key[0]=="rights" else "assignment_revisions"
            latest=c.execute(f"SELECT MAX(revision) FROM {table} WHERE entity_id=?",(key[1],)).fetchone()[0]
            require(key[2]!=latest or states.get(key)==decision,"review_state: Missing current review decision")

    def validate_database(self) -> dict:
        with self._connection() as c:
            schema=files("asset_rights_engine").joinpath("schema.sql").read_text()
            return self._validate_database(c,schema)

'''+s[end:]
p.write_text(s)
