#!/usr/bin/python

import time
import copy
import cPickle as pickle
import ast
import uuid
import threading


from UserDict import UserDict

###############################################################################
# TODO:
# 1) Need to make sure there are no race conditions
# 2) Add mutex/locks/critical sections where ever required
# 3) Manipulations on the Header functionality
# 4) Manipulations on the Metadata functionality
# 5) get() needs to error in case of expired data
###############################################################################

STATE_NEW = 'NEW'
STATE_STEADY = 'STEADY'
STATE_ERROR = 'ERROR'
STATE_EXPIRED = 'EXPIRED'


class KeyNotFoundError(Exception):
    """
    Errors due to invalid Metadata
    """
    pass


class ExpiredDataError(Exception):
    """
    Errors due to invalid Metadata
    """
    pass


class InvalidMetadataError(Exception):
    """
    Errors due to invalid Metadata
    """
    pass


class InvalidHeaderError(Exception):
    """
    Errors due to invalid Metadata
    """
    pass


class Metadata(UserDict):

    # Minimum information required for the Metadata dict to be valid
    required_keys = {
        'taskmanager_id', 'state', 'generation_id',
        'generation_time', 'missed_update_count'}

    # Valid states
    valid_states = {'NEW', 'START_BACKUP', 'METADATA_UPDATE', 'END_CYCLE'}

    def __init__(self, taskmanager_id, state='NEW', generation_id=None,
                 generation_time=None, missed_update_count=0):
        """
        Initialize Metadata object

        :type taskmanager_id: :obj:`string`
        :type state: :obj:`string`
        :type generation_id: :obj:`int`
        :type generation_time: :obj:`float`
        :type missed_update_count: :obj:`int`
        """
        UserDict.__init__(self)
        if state not in Metadata.valid_states:
            raise InvalidMetadataError('Invalid Metadata state "%s"' % state)
        if not generation_time:
            generation_time = time.time()

        self.data = {
            'taskmanager_id': taskmanager_id,
            'state': state,
            'generation_id': generation_id,
            'generation_time': generation_time,
            'missed_update_count': missed_update_count
        }


    def set_state(self, state):
        """
        Set the state for the Metadata

        :type state: :obj:`string`
        """

        if state not in Metadata.valid_states:
            raise InvalidMetadataError('%s is not a valid Metadata state' % state)
        self.data['state'] = state


class Header(UserDict):

    # Minimum information required for the Header dict to be valid
    required_keys = {
        'taskmanager_id', 'create_time', 'expiration_time',
        'scheduled_create_time', 'creator', 'schema_id'
    }

    # Default lifetime of the data if the expiration time is not specified
    default_data_lifetime = 1800

    def __init__(self, taskmanager_id, create_time=None, expiration_time=None,
                 scheduled_create_time=None, creator='module', schema_id=None):
        """
        Initialize Header object

        :type taskmanager_id: :obj:`string`
        :type create_time: :obj:`float`
        :type expiration_time: :obj:`float`
        :type scheduled_create_time: :obj:`float`
        :type creator: :obj:`string`
        :type schema_id: :obj:`int`
        """

        UserDict.__init__(self)
        if not create_time:
            create_time = time.time()
        if not expiration_time:
            expiration_time = create_time + Header.default_data_lifetime
        if not scheduled_create_time:
            scheduled_create_time = time.time()

        self.data = {
            'taskmanager_id': taskmanager_id,
            'create_time': create_time,
            'expiration_time': expiration_time,
            'scheduled_create_time': scheduled_create_time,
            'creator': creator,
            'schema_id': schema_id
        }


    def is_valid(self):
        """
        Check if the Header has minimum required information
        """

        return set(self.data.keys()).issubset(Header.required_keys)


class DataBlock(object):

    def __init__(self, dataspace, taskmanager_id=None, generation_id=None):
        """
        Initialize DataBlock object

        :type dataspace: :obj:`DataSpace`
        :type taskmanager_id: :obj:`string`
        :type generation_id: :obj:`int`
        """
 
        self.dataspace = dataspace

        # If taskmanager_id is None create new or 
        if taskmanager_id:
            self.taskmanager_id = taskmanager_id
        else:
            self.taskmanager_id = ('%s' % uuid.uuid1()).upper()

        if generation_id:
            self.generation_id = generation_id
        else:
            self.generation_id = self.dataspace.get_last_generation_id(taskmanager_id) + 1
        self._keys = []
        self.lock = threading.Lock()


    def __str__(self):
        value = {
            'taskamanger_id': self.taskmanager_id,
            'generation_id': self.generation_id,
            'keys': self._keys,
        }
        dp = {}
        for key in self._keys:
            dp[key] = self.get(key)
        value['dataproducts'] = dp
        return '%s' % value


    def __contains__(self, key):
        return key in self._keys


    def keys(self):
        return self._keys


    def put(self, key, value, header, metadata=None):
        """
        Put data into the DataBlock

        :type key: :obj:`string`
        :type value: :obj:`dict`
        :type header: :obj:`Header`
        :type metadata: :obj:`Metadata`
        """
        self.__setitem__(key, value, header, metadata=metadata)


    def get(self, key):
        """
        Return the value associated with the key in the database

        :type key: :obj:`string`
        :rtype: :obj:`dict`
        """
        return self.__getitem__(key)


    def _insert(self, key, value, header, metadata):
        """
        Insert a new product into database with header and metadata

        :type key: :obj:`string`
        :type value: :obj:`dict`
        :type header: :obj:`Header`
        :type metadata: :obj:`Metadata`
        """
        self.dataspace.insert(self.taskmanager_id, self.generation_id,
                              key, value, header, metadata)
        self._keys.append(key)


    def _update(self, key, value, header, metadata):
        """
        Update an existing product in the database with header and metadata

        :type key: :obj:`string`
        :type value: :obj:`dict`
        :type header: :obj:`Header`
        :type metadata: :obj:`Metadata`
        """
        self.dataspace.update(self.taskmanager_id, self.generation_id,
                              key, value, header, metadata)


    def __setitem__(self, key, value, header, metadata=None):
        """
        put a product in the database with header and metadata

        :type key: :obj:`string`
        :type value: :obj:`dict`
        :type header: :obj:`Header`
        :type metadata: :obj:`Metadata`
        """

        if not metadata:
            metadata = Metadata(self.taskmanager_id, state='NEW',
                                generation_id=self.generation_id,
                                generation_time=time.time(),
                                missed_update_count=0)

        if isinstance(value, dict):
            store_value = {'pickled': False, 'value': value}
        else:
            store_value = {'pickled': True, 'value': pickle.dumps(value)}

        if key in self._keys:
            # This has been already inserted, so you are working on a copy
            # that was backedup. You need to update and adjust the update
            # counter
            self._update(key, store_value, header, metadata=metadata)
        else:
            self._insert(key, store_value, header, metadata)


    def __getitem__(self, key, default=None):
        """
        Return the value associated with the key in the database

        :type key: :obj:`string`
        :type default: :obj:`dict`
        :rtype: :obj:`dict`
        """

        try:
            value_row = self.dataspace.get_dataproduct(self.taskmanager_id,
                                                       self.generation_id, key)
            value = ast.literal_eval(str(value_row['value']))
        except KeyNotFoundError, e:
            value = default
        except:
            # TODO: FINSIH with more exceptions, content
            raise

        if value.get('pickled'):
            return_value = pickle.loads(value.get('value'))
        else:
            return_value = value.get('value')
        return return_value


    def get_header(self, key):
        """
        Return the Header associated with the key in the database

        :type key: :obj:`string`
        :rtype: :obj:`Header`
        """
        try:
            header_row = self.dataspace.get_header(self.taskmanager_id,
                                                   self.generation_id, key)
            header = Header(header_row[0], create_time=header_row[3],
                            expiration_time=header_row[4],
                            scheduled_create_time=header_row[5],
                            creator=header_row[6],
                            schema_id=header_row[7])
        #except KeyNotFoundError, e:
        #    value = default
        except:
            # TODO: FINSIH with more exceptions, content
            raise
        return header


    def get_metadata(self, key):
        """
        Return the metadata associated with the key in the database

        :type key: :obj:`string`
        :rtype: :obj:`Metadata`
        """
        try:
            metadata_row = self.dataspace.get_metadata(self.taskmanager_id,
                                                       self.generation_id, key)
            metadata = Metadata(metadata_row[0], state=metadata_row[3],
                                generation_id=metadata_row[1],
                                generation_time=metadata_row[4],
                                missed_update_count=metadata_row[5])
        #except KeyNotFoundError, e:
        #    value = default
        except:
            # TODO: FINSIH with more exceptions, content
            raise
        return metadata


    def duplicate(self):
        """
        Duplicate the datablock and return this new DataBlock. The intent is
        that at the point the duplication occurs there is only information
        from the sources in the DataBlock.
        This also increments the generation_id of this DataBlock.

        TODO: Also update the header and the metadata information
        TODO: Make this threadsafe

        :rtype: :obj:`DataBlock`
        """

        dup_datablock = copy.copy(self)
        self.generation_id += 1
        dup_datablock._keys = copy.deepcopy(self._keys)
        self.dataspace.duplicate_datablock(self.taskmanager_id,
                                           dup_datablock.generation_id,
                                           self.generation_id)
        return dup_datablock


    def is_expired(self, key=None):
        """
        Check if the dataproduct for a given key or any key is expired
        """
        pass


    def mark_expired(self, expiration_time):
        """
        Set the expiration_time for the current generation of the dataproduct
        and mark it as expired if expiration_time <= current time
        
        """

        pass
