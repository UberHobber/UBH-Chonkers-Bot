# Native Stuff
from typing import Any,LiteralString

# Installed Stuff
import psycopg2

# Other Project Files
import modules.Settings as CFG
import modules.logconfig as LOG

class PostgresClass:
    """
    Initializes a PostgreSQL database connection. See Settings.py for database configuration.
    """
    def __init__(self):
        LOG.logger.debug('Connecting to database...')
        self.database = psycopg2.connect(host=CFG.DB_HOST,port=CFG.DB_PORT,database=CFG.DB_NAME,user=CFG.DB_USR,password=CFG.DB_PASS)
        self.cursor = self.database.cursor()

    def Close(self):
        """
        LBA: Closes the database cursor and connection.
        """
        LOG.logger.debug('Closing SQL Database...')
        self.cursor.close()
        self.database.close()

    def ClearDB(self,tables:list[str]):
        """
        LBA: Clears database tables.

        :param tables: List of database tables to delete all entries from
        :type tables: List of strings
        """
        for table in tables:
            self.cursor.execute(f'DELETE FROM {table}')
            self.database.commit()
            LOG.logger.info(f'Data from {table} deleted.')

def InsertEntries(cursor:psycopg2.extensions.cursor,table:str,data_list:list[dict[str,Any]],conflict:str|None=None) -> None:
    """
    Inserts a given list of dictionaries into a target table.
    
    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: Target table
    :type table: String
    :param data_list: A list of entries to input into the target table.
    :type data_list: List of Dictionaries
    :param conflict: A column name that is either a Primary Key, or contains a Unique Constraint
    :type conflict: String
    """
    try:
        LOG.logger.debug(f'{len(data_list)} item(s) to add to table {table} in database.')
        for item in data_list:
            columns: str = ', '.join(item.keys())
            placeholders: LiteralString = ', '.join(['%s' for _ in item])
            values = list(item.values())
            if conflict:
                conflict_text = f" ON CONFLICT ({conflict}) DO NOTHING"
                query: str = f'INSERT INTO {table} ({columns}) VALUES ({placeholders}){conflict_text}'
            else:
                query: str = f'INSERT INTO {table} ({columns}) VALUES ({placeholders})'

            

            if CFG.DB_VERBOSE == True:
                LOG.logger.info(query)
                LOG.logger.info(values)

            cursor.execute(query,values)
    except Exception as e:
        LOG.logger.error(f'Query: {query} ({type(query)})\nValues: {values} ({type(values)})\n')
        raise e

def UpdateEntry(cursor:psycopg2.extensions.cursor,table:str,data_column:str,data_value:Any,filter_column:str,filter_value:Any):
    """
    Updates an entry with a new value for a single column.
    
    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: Target table
    :type table: String
    :param data_column: The column that will be targeted
    :type data_column: String
    :param data_value: The value to update into that row's column
    :type data_value: Acceptable datatype for your column
    :param filter_column: Column to target for filter
    :type filter_column: String
    :param filter_value: Value of column to filter by
    :type filter_value: Acceptable datatype for your column
    """
    try:
        LOG.logger.debug(f'Updating {data_column} in table {table} in database.')

        values = (data_value, filter_value)

        query: str = f'UPDATE {table} SET {data_column} = %s WHERE {filter_column} = %s'

        if CFG.DB_VERBOSE == True:
            LOG.logger.info(query)
            LOG.logger.info(values)

        cursor.execute(query,values)
    except Exception as e:
        LOG.logger.error(f'Query: {query} ({type(query)})\nValues: {values} ({type(values)})\n')
        raise e

def DeleteEntries(cursor:psycopg2.extensions.cursor,table:str,filter:dict[str,Any]|None=None) -> None:
    """
    Deletes an entry in the target table matching a given filter. Deletes ALL entries if no filter given.

    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: Target table
    :type table: String
    :param filter: Deletes entries that contain specific values in specific columns. If non specified, delete EVERYTHING in table.
    :type filter: Dictionary of {[Column] , [Value]}
    """

    base_query = f'DELETE FROM {table}'

    try:
        if filter != None:
            column_list:str = " AND ".join([f'{col} = %s' for col in filter.keys()])
            values = list(filter.values())

            query: str = f'{base_query} WHERE {column_list}'

            if CFG.DB_VERBOSE == True:
                LOG.logger.info(query)
                LOG.logger.info(values)

            cursor.execute(query,values)
        else:
            LOG.logger.info(base_query)
            cursor.execute(base_query)
    except Exception as e:
        LOG.logger.error(f'Query: {query}\nValues: {values}\n')
        raise e

def GetEntries(cursor:psycopg2.extensions.cursor,table:str,columns:str='*',filter:dict[str,Any]|None=None) -> list[dict[str, Any]]:
    """
    Retrieves entries from a given table. Can specify columns and various filters.
    
    :param cursor: Database cursor object to execute commands.
    :type cursor: Cursor
    :param table: Table name
    :type table: String
    :param columns: Retrieves values from specified columns. If none are specified, get all values from entries.
    :type columns: String formatted as '[ColName], [Colname], etc.'
    :param filter: Get results that contain specific values in specific columns. If non specified, don't filter anything
    :type filter: Dictionary of {[Column] , [Value]}
    :return: List of entries
    :rtype: Format matching Entry Objects (List of Dictionaries {[Colname] , [Value]})
    """
    base_query: str = f'SELECT {columns} FROM {table}'

    if filter != None:
        
        column_list:str = " AND ".join([f'{col} = %s' for col in filter.keys()])

        values = list(filter.values())

        query = f'{base_query} WHERE {column_list}'
        cursor.execute(query,values)
    else:
        cursor.execute(base_query)

    if cursor.description is not None:
        entry_columns:list[str] = [description[0] for description in cursor.description]
    entries = cursor.fetchall()
    results:list[dict[str,Any]] = []

    for row in entries:
        row_dict = dict(zip(entry_columns,row))
        results.append(row_dict)

    return results