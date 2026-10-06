"""Consistent full-business JSONL stream; never output private rows to logs."""
import json
from full_business_contract import BUSINESS_TABLES,validate_source_tables


def snapshot_sql(tables):
    validate_source_tables(tables)
    tables=tuple(t for t in BUSINESS_TABLES if t in tables)
    table_json=json.dumps(list(tables),separators=(',',':'))
    columns=' UNION ALL '.join("SELECT '"+t+"' tab, jsonb_agg(jsonb_build_object('name',attname,'type',format_type(atttypid,atttypmod),'required',attnotnull,'generated',attgenerated) ORDER BY attnum) columns FROM pg_attribute WHERE attrelid='public."+t+"'::regclass AND attnum>0 AND NOT attisdropped" for t in tables)
    statements=["BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL statement_timeout='120s'; SET LOCAL TIMEZONE='UTC'; SET LOCAL DATESTYLE='ISO,YMD'; SET LOCAL extra_float_digits=3;",
                "SELECT jsonb_build_object('kind','header','schema','full-business-snapshot/v1','tables','"+table_json+"'::jsonb,'columns',(SELECT jsonb_object_agg(tab,columns) FROM ("+columns+") columns));"]
    for table in tables:
        statements.append("SELECT jsonb_build_object('kind','row','table','"+table+"','row',to_jsonb(t)) FROM public.\""+table+"\" t;")
    counts=' UNION ALL '.join("SELECT '"+t+"' tab,count(*) total FROM public.\""+t+'"' for t in tables)
    statements += ["SELECT jsonb_build_object('kind','footer','counts',(SELECT jsonb_object_agg(tab,total) FROM ("+counts+") counts));",'COMMIT;']
    return '\n'.join(statements)+'\n'


class SnapshotValidator:
    def __init__(self,tables):
        self.tables=tuple(t for t in BUSINESS_TABLES if t in tables)
        validate_source_tables(self.tables)
        self.counts={t:0 for t in self.tables};self.header=False;self.footer=False;self.columns={}

    def accept(self,line):
        value=json.loads(line)
        if not isinstance(value,dict):raise ValueError('Snapshot record is not an object')
        kind=value.get('kind')
        if kind=='header' and not self.header and not self.footer:
            if (value.get('schema')!='full-business-snapshot/v1' or value.get('tables')!=list(self.tables)
                    or set(value.get('columns',{}))!=set(self.tables)):
                raise ValueError('Snapshot metadata differs from reviewed source contract')
            for table,columns in value['columns'].items():
                if not isinstance(columns,list) or not columns or any(not isinstance(c,dict) or not isinstance(c.get('name'),str) or not isinstance(c.get('type'),str) for c in columns):
                    raise ValueError('Source column metadata is incomplete')
                names=[c['name'] for c in columns]
                if len(names)!=len(set(names)):
                    raise ValueError('Source column metadata is duplicated')
                self.columns[table]=set(names)
            self.header=True
        elif kind=='row' and self.header and not self.footer:
            table=value.get('table')
            if table not in self.counts or not isinstance(value.get('row'),dict):
                raise ValueError('Snapshot row table or format is invalid')
            if set(value['row'])!=self.columns[table]:
                raise ValueError('Source row field coverage differs from its catalog')
            self.counts[table]+=1
        elif kind=='footer' and self.header and not self.footer:
            if value.get('counts')!=self.counts:
                raise ValueError('Snapshot stream count differs from consistent source counts')
            self.footer=True
        else:raise ValueError('Snapshot record order is invalid')

    def finish(self):
        if not self.header or not self.footer:raise ValueError('Snapshot is incomplete')
        return dict(self.counts)


def visibility_guard_sql(tables):
    validate_source_tables(tables)
    names=",".join("'"+t+"'" for t in BUSINESS_TABLES if t in tables)
    return """BEGIN READ ONLY;
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=current_user AND current_user='readonly_release' AND rolcanlogin
 AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication
 AND NOT rolbypassrls AND NOT rolinherit)
 AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p')
 AND (has_table_privilege(current_user,c.oid,'INSERT') OR has_table_privilege(current_user,c.oid,'UPDATE')
 OR has_table_privilege(current_user,c.oid,'DELETE') OR has_table_privilege(current_user,c.oid,'TRUNCATE')))
 AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user))
 AND NOT EXISTS (
 SELECT 1 FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relname IN ("""+names+""")
 AND c.relrowsecurity AND (
 NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid
 AND p.polname='release_initialization_readonly' AND p.polcmd='r' AND p.polpermissive
 AND p.polroles=ARRAY[(SELECT oid FROM pg_roles WHERE rolname=current_user)]
 AND pg_get_expr(p.polqual,p.polrelid)='true' AND p.polwithcheck IS NULL)
 OR EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid AND NOT p.polpermissive AND p.polcmd IN ('r','*')
 AND (0::oid=ANY(p.polroles) OR (SELECT oid FROM pg_roles WHERE rolname=current_user)=ANY(p.polroles)))
 ));
COMMIT;
"""
