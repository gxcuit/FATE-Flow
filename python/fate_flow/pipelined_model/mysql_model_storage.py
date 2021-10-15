#
#  Copyright 2019 The FATE Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import sys
import datetime
from copy import deepcopy

from peewee import Model, CharField, BigIntegerField, TextField, CompositeKey, IntegerField
from playhouse.pool import PooledMySQLDatabase

from fate_flow.pipelined_model.pipelined_model import PipelinedModel
from fate_flow.pipelined_model.model_storage_base import ModelStorageBase
from fate_flow.utils.log_utils import getLogger
from fate_arch.common.base_utils import current_timestamp, serialize_b64, deserialize_b64
from fate_arch.metastore.base_model import LongTextField


LOGGER = getLogger()
DB = PooledMySQLDatabase(None)

SLICE_MAX_SIZE = 1024*1024*8


class MysqlModelStorage(ModelStorageBase):

    def store(self, model_id: str, model_version: str, store_address: dict, force_update: bool = False):
        """
        Store the model from local cache to mysql
        :param model_id:
        :param model_version:
        :param store_address:
        :param force_update:
        :return:
        """
        try:
            self.get_connection(store_address)
            DB.create_tables([MachineLearningModel])
            model = PipelinedModel(model_id, model_version)

            LOGGER.info(f"Starting store model {model_id} {model_version}.")
            with DB.connection_context():
                with open(model.packaging_model(), "rb") as fr:
                    slice_index = 0
                    while True:
                        content = fr.read(SLICE_MAX_SIZE)
                        if content:
                            model_in_table = MachineLearningModel()
                            model_in_table.f_create_time = current_timestamp()
                            model_in_table.f_model_id = model_id
                            model_in_table.f_model_version = model_version
                            model_in_table.f_content = serialize_b64(content, to_str=True)
                            model_in_table.f_size = sys.getsizeof(model_in_table.f_content)
                            model_in_table.f_slice_index = slice_index
                            if force_update:
                                model_in_table.save(only=[MachineLearningModel.f_content, MachineLearningModel.f_size,
                                                          MachineLearningModel.f_update_time, MachineLearningModel.f_slice_index])
                                LOGGER.info(f"Update model {model_id} {model_version} slice index {slice_index} content.")
                            else:
                                model_in_table.save(force_insert=True)
                                LOGGER.info(f"Insert model {model_id} {model_version} slice index {slice_index} content.")
                            slice_index += 1
                        else:
                            break
                    LOGGER.info(f"Store model {model_id} {model_version} to mysql successfully")
            self.close_connection()
        except Exception as e:
            LOGGER.exception(e)
            raise Exception(f"Store model {model_id} {model_version} to mysql failed")

    def restore(self, model_id: str, model_version: str, store_address: dict):
        """
        Restore model from mysql to local cache
        :param model_id:
        :param model_version:
        :param store_address:
        :return:
        """
        try:
            self.get_connection(store_address)
            model = PipelinedModel(model_id, model_version)
            with DB.connection_context():
                models_in_tables = MachineLearningModel.select().where(MachineLearningModel.f_model_id == model_id,
                                                                       MachineLearningModel.f_model_version == model_version).\
                    order_by(MachineLearningModel.f_slice_index)
                if not models_in_tables:
                    raise Exception(f"Restore model {model_id} {model_version} from mysql failed: "
                                    f"can not found model in table.")
                f_content = ''
                for models_in_table in models_in_tables:
                    if not f_content:
                        f_content = models_in_table.f_content
                    else:
                        f_content += models_in_table.f_content
                model_archive_data = deserialize_b64(f_content)
                if not model_archive_data:
                    raise Exception(f"Restore model {model_id} {model_version} from mysql failed: "
                                    f"can not get model archive data.")
                with open(model.archive_model_file_path, "wb") as fw:
                    fw.write(model_archive_data)
                model.unpack_model(model.archive_model_file_path)
                LOGGER.info(f"Restore model to {model.archive_model_file_path} from mysql successfully")
            self.close_connection()
        except Exception as e:
            LOGGER.exception(e)
            raise Exception(f"Restore model {model_id} {model_version} from mysql failed")

    @staticmethod
    def get_connection(store_address: dict):
        store_address = deepcopy(store_address)
        db_name = store_address.pop('database')
        del store_address['storage']
        DB.init(db_name, **store_address)

    @staticmethod
    def close_connection():
        try:
            if DB:
                DB.close()
        except Exception as e:
            LOGGER.exception(e)


class DataBaseModel(Model):
    class Meta:
        database = DB

    def to_json(self):
        return self.__dict__['__data__']

    def save(self, *args, **kwargs):
        if hasattr(self, "f_update_date"):
            self.f_update_date = datetime.datetime.now()
        if hasattr(self, "f_update_time"):
            self.f_update_time = current_timestamp()
        super(DataBaseModel, self).save(*args, **kwargs)


class MachineLearningModel(DataBaseModel):
    f_model_id = CharField(max_length=100, index=True)
    f_model_version = CharField(max_length=100, index=True)
    f_size = BigIntegerField(default=0)
    f_create_time = BigIntegerField(default=0)
    f_update_time = BigIntegerField(default=0)
    f_description = TextField(null=True, default='')
    f_content = LongTextField(default='')
    f_slice_index = IntegerField(default=0, index=True)

    class Meta:
        db_table = "t_machine_learning_model"
        primary_key = CompositeKey('f_model_id', 'f_model_version', 'f_slice_index')
