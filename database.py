import psycopg2
from config import DB_CONFIG

class Database:
    def __init__(self):
        self.connection = None
        
    def connect(self):
        try:
            self.connection = psycopg2.connect(**DB_CONFIG)
            return self.connection
        except Exception as e:
            print(f"Erro ao conectar: {e}")
            return None
            
    def execute_query(self, query, params=None):
        conn = self.connect()
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    if query.strip().lower().startswith('select'):
                        result = cursor.fetchall()
                        columns = [desc[0] for desc in cursor.description]
                        return result, columns
                    conn.commit()
                    return None, None
            except Exception as e:
                print(f"Erro na query: {e}")
                return None, None
            finally:
                conn.close()
        return None, None
    
    def get_unique_values(self, column_name, table_name):
        query = f"SELECT DISTINCT {column_name} FROM {table_name} ORDER BY {column_name}"
        result, _ = self.execute_query(query)
        return [item[0] for item in result] if result else []