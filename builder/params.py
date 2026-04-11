import time
from utils.tou_tiao_utils import generate_msToken, splice_url, generate_a_bogus


class Params:
    def __init__(self):
        self.params = {}

    def add_param_by_dict(self, params):
        self.params.update(params)

    def add_param(self, key, value):
        self.params[key] = value

    def get(self):
        return self.params

    def with_a_bogus(self, data=None):
        query = splice_url(self.get())
        if data is not None:
            data = splice_url(data)
        else:
            data = ''
        abogus = generate_a_bogus(query, data)
        self.add_param('a_bogus', abogus)
        return self

    def with_ms_token(self):
        msToken = generate_msToken()
        self.params['msToken'] = msToken
        return self
class ParamsBuilder:
    base_url = ''
    @staticmethod
    def build_get_user_info_param():
        params = Params()
        params.add_param_by_dict({
            "app_id": "12",
            "_t": str(int(time.time() * 1000))
        })
        return params

