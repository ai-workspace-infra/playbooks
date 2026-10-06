"""Email-bound UAT identity mapping, preserving every source Proxy UUID.

User UUIDs are environment-local. Full business import rewrites user references
through this map; a Proxy UUID is an invariant, never an identity matching key.
No private identity values are included in validation errors.
"""
import uuid


def email_key(value):
    if not isinstance(value,str): raise ValueError('Identity email is not a string')
    result=value.strip().lower()
    if not result or result.count('@')!=1 or any(c.isspace() for c in result):
        raise ValueError('Identity email key is missing or invalid')
    return result


def index_users(users):
    by_email={}; by_id={}; by_proxy={}
    for row in users:
        key=email_key(row.get('email'))
        identity=str(uuid.UUID(row['uuid']))
        proxy=str(uuid.UUID(row['proxy_uuid']))
        if key in by_email or identity in by_id or proxy in by_proxy:
            raise ValueError('Identity email, UUID or Proxy UUID is duplicated')
        normalized={'email_key':key,'uuid':identity,'proxy_uuid':proxy}
        by_email[key]=normalized;by_id[identity]=normalized;by_proxy[proxy]=normalized
    return by_email,by_id,by_proxy


def build_email_identity_map(source_users,target_users,uuid_factory=uuid.uuid4):
    sources,source_ids,source_proxies=index_users(source_users)
    targets,target_ids,target_proxies=index_users(target_users)
    reserved=set(source_ids)|set(source_proxies)|set(target_ids)|set(target_proxies)
    mapping={}; proxy_by_target={}
    for key,source in sources.items():
        target=targets.get(key)
        proxy_owner=target_proxies.get(source['proxy_uuid'])
        if proxy_owner and proxy_owner['email_key']!=key:
            raise ValueError('Source Proxy UUID belongs to a different target email')
        if target:
            target_id=target['uuid']
        else:
            target_id=None
            for _ in range(16):
                candidate=str(uuid.UUID(str(uuid_factory())))
                if candidate not in reserved:
                    target_id=candidate;reserved.add(candidate);break
            if target_id is None: raise ValueError('Unable to assign a unique target identity')
        mapping[source['uuid']]=target_id
        proxy_by_target[target_id]=source['proxy_uuid']
    if len(set(mapping.values()))!=len(mapping):
        raise ValueError('Multiple source users resolve to the same target identity')
    return mapping,proxy_by_target


def remap_user_reference(value,mapping):
    key=str(uuid.UUID(str(value)))
    if key not in mapping:
        raise ValueError('Business row references a user outside the source snapshot')
    return mapping[key]
