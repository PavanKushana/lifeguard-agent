-- init.sql: the shop's database, made the first time the db container starts.
CREATE TABLE products (id SERIAL PRIMARY KEY, name TEXT NOT NULL, price NUMERIC(8, 2) NOT NULL);
CREATE TABLE orders (id SERIAL PRIMARY KEY, product_id INT REFERENCES products(id), created_at TIMESTAMPTZ DEFAULT now());
INSERT INTO products (name, price) VALUES ('Rubber duck', 4.99), ('Beach towel', 19.90), ('Sun hat', 14.50);
