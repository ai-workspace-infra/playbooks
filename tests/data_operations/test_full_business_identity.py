import pathlib,sys,unittest,uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'scripts/data_operations'))
from full_business_identity import build_email_identity_map, remap_user_reference


def u(number,email,proxy):
    return {'uuid':str(uuid.UUID(int=number)),'email':email,'proxy_uuid':str(uuid.UUID(int=proxy))}


class FullBusinessEmailIdentity(unittest.TestCase):
    def test_same_email_different_uuid_preserves_target_id_source_proxy(self):
        source=u(1,' Person@Example.invalid ',10);target=u(2,'person@example.invalid',20)
        mapping,proxies=build_email_identity_map([source],[target])
        self.assertEqual(mapping,{source['uuid']:target['uuid']})
        self.assertEqual(proxies,{target['uuid']:source['proxy_uuid']})
        self.assertEqual(remap_user_reference(source['uuid'],mapping),target['uuid'])

    def test_same_proxy_different_email_is_not_a_match(self):
        with self.assertRaises(ValueError):
            build_email_identity_map([u(1,'a@example.invalid',10)],[u(2,'b@example.invalid',10)])

    def test_new_user_receives_target_local_uuid_and_unchanged_proxy(self):
        source=u(1,'a@example.invalid',10)
        mapping,proxies=build_email_identity_map([source],[],lambda:uuid.UUID(int=99))
        self.assertEqual(mapping[source['uuid']],str(uuid.UUID(int=99)))
        self.assertEqual(proxies[str(uuid.UUID(int=99))],source['proxy_uuid'])

    def test_duplicate_email_rejected_before_writes(self):
        with self.assertRaises(ValueError):
            build_email_identity_map([u(1,'a@example.invalid',10),u(2,' A@example.invalid ',20)],[])

    def test_unknown_reference_and_uuid_collisions_rejected(self):
        with self.assertRaises(ValueError):remap_user_reference(str(uuid.UUID(int=123)),{})
        with self.assertRaises(ValueError):build_email_identity_map([u(1,'a@example.invalid',10)],[],lambda:uuid.UUID(int=1))

    def test_target_only_email_rejected_without_deletion(self):
        with self.assertRaises(ValueError):
            build_email_identity_map([u(1,'a@example.invalid',10)],
                                     [u(2,'a@example.invalid',20),u(3,'extra@example.invalid',30)])

    def test_target_count_converges_to_source_through_missing_email_creation(self):
        source=[u(1,'a@example.invalid',10),u(2,'b@example.invalid',20)]
        target=[u(3,'a@example.invalid',30)]
        mapping,proxies=build_email_identity_map(source,target,lambda:uuid.UUID(int=99))
        self.assertEqual(len(mapping),len(source))
        self.assertEqual(len(set(mapping.values())),len(source))
        self.assertEqual(mapping[source[0]['uuid']],target[0]['uuid'])
        self.assertEqual(set(proxies.values()),{row['proxy_uuid'] for row in source})

    def test_second_hop_keeps_proxy_even_when_user_uuid_changes_again(self):
        prod=u(1,'a@example.invalid',10);serverless=u(2,'a@example.invalid',20);selfhost=u(3,'a@example.invalid',30)
        first,proxy=build_email_identity_map([prod],[serverless])
        forwarded=dict(serverless,proxy_uuid=proxy[serverless['uuid']])
        second,final_proxy=build_email_identity_map([forwarded],[selfhost])
        self.assertEqual(final_proxy[selfhost['uuid']],prod['proxy_uuid'])
        self.assertEqual(second[serverless['uuid']],selfhost['uuid'])


if __name__=='__main__':unittest.main()
