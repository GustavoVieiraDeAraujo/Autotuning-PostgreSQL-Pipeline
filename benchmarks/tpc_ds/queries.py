"""
As 99 queries do benchmark TPC-DS com valores de substituição fixos
(baseados nos parâmetros de qualificação da especificação TPC-DS v2.x).

Cada entrada é um dict com:
    id       : int - número da query (1–99)
    name     : str - nome descritivo
    sql      : str - SQL pronto para executar
    setup    : str - DDL executado antes da query (opcional)
    teardown : str - DDL executado após a query (opcional)
"""

QUERIES: list[dict] = (
[
    {"id": 1, "name": "Customer Returns Store", "sql": """
WITH customer_total_return AS (
    SELECT sr_customer_sk AS ctr_customer_sk, sr_store_sk AS ctr_store_sk,
           SUM(sr_return_amt) AS ctr_total_return
    FROM store_returns, date_dim
    WHERE sr_returned_date_sk = d_date_sk AND d_year = 2000
    GROUP BY sr_customer_sk, sr_store_sk
)
SELECT c_customer_id FROM customer_total_return ctr1, store, customer
WHERE ctr1.ctr_total_return > (
    SELECT AVG(ctr_total_return) * 1.2 FROM customer_total_return ctr2
    WHERE ctr1.ctr_store_sk = ctr2.ctr_store_sk
)
  AND s_store_sk = ctr1.ctr_store_sk AND s_state = 'TN'
  AND ctr1.ctr_customer_sk = c_customer_sk
ORDER BY c_customer_id LIMIT 100;
"""},
    {"id": 2, "name": "Catalog vs Web Sales YoY", "sql": """
WITH wscs AS (
    SELECT sold_date_sk, sales_price FROM (
        SELECT ws_sold_date_sk AS sold_date_sk, ws_ext_sales_price AS sales_price FROM web_sales
        UNION ALL
        SELECT cs_sold_date_sk, cs_ext_sales_price FROM catalog_sales
    ) x
),
wswscs AS (
    SELECT d_week_seq,
           SUM(CASE WHEN d_day_name='Sunday'    THEN sales_price ELSE NULL END) sun_sales,
           SUM(CASE WHEN d_day_name='Monday'    THEN sales_price ELSE NULL END) mon_sales,
           SUM(CASE WHEN d_day_name='Tuesday'   THEN sales_price ELSE NULL END) tue_sales,
           SUM(CASE WHEN d_day_name='Wednesday' THEN sales_price ELSE NULL END) wed_sales,
           SUM(CASE WHEN d_day_name='Thursday'  THEN sales_price ELSE NULL END) thu_sales,
           SUM(CASE WHEN d_day_name='Friday'    THEN sales_price ELSE NULL END) fri_sales,
           SUM(CASE WHEN d_day_name='Saturday'  THEN sales_price ELSE NULL END) sat_sales
    FROM wscs, date_dim WHERE sold_date_sk = d_date_sk GROUP BY d_week_seq
)
SELECT d_week_seq1,
       ROUND(sun_sales1/sun_sales2,2), ROUND(mon_sales1/mon_sales2,2),
       ROUND(tue_sales1/tue_sales2,2), ROUND(wed_sales1/wed_sales2,2),
       ROUND(thu_sales1/thu_sales2,2), ROUND(fri_sales1/fri_sales2,2),
       ROUND(sat_sales1/sat_sales2,2)
FROM
  (SELECT wswscs.d_week_seq d_week_seq1, sun_sales sun_sales1, mon_sales mon_sales1,
          tue_sales tue_sales1, wed_sales wed_sales1, thu_sales thu_sales1,
          fri_sales fri_sales1, sat_sales sat_sales1
   FROM wswscs,date_dim WHERE date_dim.d_week_seq=wswscs.d_week_seq AND d_year=2001) y,
  (SELECT wswscs.d_week_seq d_week_seq2, sun_sales sun_sales2, mon_sales mon_sales2,
          tue_sales tue_sales2, wed_sales wed_sales2, thu_sales thu_sales2,
          fri_sales fri_sales2, sat_sales sat_sales2
   FROM wswscs,date_dim WHERE date_dim.d_week_seq=wswscs.d_week_seq AND d_year=2002) z
WHERE d_week_seq1=d_week_seq2-53 ORDER BY d_week_seq1;
"""},
    {"id": 3, "name": "Store Sales by Brand", "sql": """
SELECT dt.d_year, item.i_brand_id brand_id, item.i_brand brand,
       SUM(ss_ext_sales_price) sum_agg
FROM date_dim dt, store_sales, item
WHERE dt.d_date_sk = store_sales.ss_sold_date_sk
  AND store_sales.ss_item_sk = item.i_item_sk
  AND item.i_manufact_id = 128 AND dt.d_moy = 11
GROUP BY dt.d_year, item.i_brand, item.i_brand_id
ORDER BY dt.d_year, sum_agg DESC, brand_id LIMIT 100;
"""},
    {"id": 4, "name": "Customer Cross-Channel Annual Spend", "sql": """
WITH year_total AS (
    SELECT c_customer_id customer_id, c_first_name customer_first_name,
           c_last_name customer_last_name, c_preferred_cust_flag customer_preferred_cust_flag,
           c_birth_country customer_birth_country, c_login customer_login,
           c_email_address customer_email_address, d_year dyear,
           SUM(ss_ext_list_price-ss_ext_discount_amt) year_total, 's' sale_type
    FROM customer, store_sales, date_dim
    WHERE c_customer_sk=ss_customer_sk AND ss_sold_date_sk=d_date_sk
    GROUP BY c_customer_id,c_first_name,c_last_name,c_preferred_cust_flag,
             c_birth_country,c_login,c_email_address,d_year
    UNION ALL
    SELECT c_customer_id,c_first_name,c_last_name,c_preferred_cust_flag,
           c_birth_country,c_login,c_email_address,d_year,
           SUM(ws_ext_list_price-ws_ext_discount_amt) year_total, 'w' sale_type
    FROM customer, web_sales, date_dim
    WHERE c_customer_sk=ws_bill_customer_sk AND ws_sold_date_sk=d_date_sk
    GROUP BY c_customer_id,c_first_name,c_last_name,c_preferred_cust_flag,
             c_birth_country,c_login,c_email_address,d_year
)
SELECT t_s_secyear.customer_id, t_s_secyear.customer_first_name,
       t_s_secyear.customer_last_name, t_s_secyear.customer_birth_country
FROM year_total t_s_firstyear, year_total t_s_secyear,
     year_total t_w_firstyear, year_total t_w_secyear
WHERE t_s_firstyear.customer_id=t_s_secyear.customer_id
  AND t_s_firstyear.customer_id=t_w_firstyear.customer_id
  AND t_s_firstyear.customer_id=t_w_secyear.customer_id
  AND t_s_firstyear.sale_type='s' AND t_w_firstyear.sale_type='w'
  AND t_s_secyear.sale_type='s'   AND t_w_secyear.sale_type='w'
  AND t_s_firstyear.dyear=2001 AND t_s_secyear.dyear=2002
  AND t_w_firstyear.dyear=2001 AND t_w_secyear.dyear=2002
  AND t_s_firstyear.year_total > 0 AND t_w_firstyear.year_total > 0
  AND CASE WHEN t_w_firstyear.year_total>0 THEN t_w_secyear.year_total/t_w_firstyear.year_total ELSE NULL END
    > CASE WHEN t_s_firstyear.year_total>0 THEN t_s_secyear.year_total/t_s_firstyear.year_total ELSE NULL END
ORDER BY t_s_secyear.customer_id, t_s_secyear.customer_first_name,
         t_s_secyear.customer_last_name, t_s_secyear.customer_birth_country LIMIT 100;
"""},
    {"id": 5, "name": "Net Revenue by Channel", "sql": """
WITH ssr AS (
    SELECT s_store_id,
           SUM(sales_price) AS sales, SUM(profit) AS profit,
           SUM(return_amt) AS returns, SUM(net_loss) AS profit_loss
    FROM (
        SELECT ss_store_sk store_sk, ss_sold_date_sk date_sk,
               ss_ext_sales_price sales_price, ss_net_profit profit,
               CAST(0 AS DECIMAL(7,2)) return_amt, CAST(0 AS DECIMAL(7,2)) net_loss
        FROM store_sales
        UNION ALL
        SELECT sr_store_sk, sr_returned_date_sk,
               CAST(0 AS DECIMAL(7,2)), CAST(0 AS DECIMAL(7,2)),
               sr_return_amt, sr_net_loss
        FROM store_returns
    ) salesreturns, date_dim, store
    WHERE date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-08-23' AS DATE) AND CAST('2000-08-23' AS DATE)+INTERVAL'14 days'
      AND store_sk=s_store_sk
    GROUP BY s_store_id
),
csr AS (
    SELECT cp_catalog_page_id,
           SUM(sales_price) sales, SUM(profit) profit,
           SUM(return_amt) returns, SUM(net_loss) profit_loss
    FROM (
        SELECT cs_catalog_page_sk page_sk, cs_sold_date_sk date_sk,
               cs_ext_sales_price sales_price, cs_net_profit profit,
               CAST(0 AS DECIMAL(7,2)) return_amt, CAST(0 AS DECIMAL(7,2)) net_loss
        FROM catalog_sales
        UNION ALL
        SELECT cr_catalog_page_sk, cr_returned_date_sk,
               CAST(0 AS DECIMAL(7,2)), CAST(0 AS DECIMAL(7,2)),
               cr_return_amount, cr_net_loss
        FROM catalog_returns
    ) salesreturns, date_dim, catalog_page
    WHERE date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-08-23' AS DATE) AND CAST('2000-08-23' AS DATE)+INTERVAL'14 days'
      AND page_sk=cp_catalog_page_sk
    GROUP BY cp_catalog_page_id
),
wsr AS (
    SELECT web_site_id,
           SUM(sales_price) sales, SUM(profit) profit,
           SUM(return_amt) returns, SUM(net_loss) profit_loss
    FROM (
        SELECT ws_web_site_sk wsite_sk, ws_sold_date_sk date_sk,
               ws_ext_sales_price sales_price, ws_net_profit profit,
               CAST(0 AS DECIMAL(7,2)) return_amt, CAST(0 AS DECIMAL(7,2)) net_loss
        FROM web_sales
        UNION ALL
        SELECT wr_web_page_sk, wr_returned_date_sk,
               CAST(0 AS DECIMAL(7,2)), CAST(0 AS DECIMAL(7,2)),
               wr_return_amt, wr_net_loss
        FROM web_returns
    ) salesreturns, date_dim, web_site
    WHERE date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-08-23' AS DATE) AND CAST('2000-08-23' AS DATE)+INTERVAL'14 days'
      AND wsite_sk=web_site_sk
    GROUP BY web_site_id
)
SELECT channel, id, SUM(sales) sales, SUM(returns) returns, SUM(profit) profit
FROM (
    SELECT 'store channel' channel,'store'||s_store_id id, sales, returns, profit_loss profit FROM ssr
    UNION ALL
    SELECT 'catalog channel','catalog_page'||cp_catalog_page_id, sales, returns, profit_loss FROM csr
    UNION ALL
    SELECT 'web channel','web_site'||web_site_id, sales, returns, profit_loss FROM wsr
) x
GROUP BY ROLLUP(channel,id) ORDER BY channel,id LIMIT 100;
"""},
    {"id": 6, "name": "Customers by State with High Spend", "sql": """
SELECT a.ca_state state, COUNT(*) cnt
FROM customer_address a, customer c, store_sales s, date_dim d, item i
WHERE a.ca_address_sk=c.c_current_addr_sk AND c.c_customer_sk=s.ss_customer_sk
  AND s.ss_sold_date_sk=d.d_date_sk AND s.ss_item_sk=i.i_item_sk
  AND d.d_month_seq=(SELECT DISTINCT d_month_seq FROM date_dim WHERE d_year=2001 AND d_moy=1)
  AND i.i_current_price > 1.2*(SELECT AVG(j.i_current_price) FROM item j WHERE j.i_category=i.i_category)
GROUP BY a.ca_state HAVING COUNT(*)>=10
ORDER BY cnt DESC, a.ca_state LIMIT 10;
"""},
    {"id": 7, "name": "Store Sales Demographics", "sql": """
SELECT i_item_id, AVG(ss_quantity) agg1, AVG(ss_list_price) agg2,
       AVG(ss_coupon_amt) agg3, AVG(ss_sales_price) agg4
FROM store_sales, customer_demographics, date_dim, item, promotion
WHERE ss_sold_date_sk=d_date_sk AND ss_item_sk=i_item_sk
  AND ss_cdemo_sk=cd_demo_sk AND ss_promo_sk=p_promo_sk
  AND cd_gender='M' AND cd_marital_status='S' AND cd_education_status='College'
  AND (p_channel_email='N' OR p_channel_event='N') AND d_year=2000
GROUP BY i_item_id ORDER BY i_item_id LIMIT 100;
"""},
    {"id": 8, "name": "Net Revenue from Stores in Specific ZIP Codes", "sql": """
SELECT s_store_name, SUM(ss_net_profit)
FROM store_sales, date_dim, store,
     (SELECT ca_zip FROM (
         SELECT SUBSTR(ca_zip,1,5) ca_zip FROM customer_address
         WHERE SUBSTR(ca_zip,1,5) IN (
             '24128','76232','65084','87816','83926','77556','20548','26114',
             '57678','66455','41207','26161','00212','90578','73602','39559',
             '41062','48442','48408','71526','56804','32412','58843','43339',
             '42775','31234','56569','85268','71791','57029','73167','54561',
             '53034','41796','75625','37272','51997','60099','69016','69227',
             '25263','65393','52005','97294','16226','62964','10174','80583',
             '88150','37615')
         INTERSECT
         SELECT ca_zip FROM (
             SELECT SUBSTR(ca_zip,1,5) ca_zip, COUNT(*) cnt
             FROM customer_address, customer
             WHERE ca_address_sk=c_current_addr_sk AND c_preferred_cust_flag='Y'
             GROUP BY ca_zip HAVING COUNT(*)>10) A1
     ) A2) V1
WHERE ss_store_sk=s_store_sk AND ss_sold_date_sk=d_date_sk
  AND d_qoy=2 AND d_year=2000
  AND SUBSTR(s_zip,1,5)=SUBSTR(V1.ca_zip,1,5)
GROUP BY s_store_name ORDER BY s_store_name LIMIT 100;
"""},
    {"id": 9, "name": "Conditional Average Aggregation", "sql": """
SELECT
  CASE WHEN (SELECT COUNT(*) FROM store_sales WHERE ss_quantity BETWEEN 1 AND 20)>74129
       THEN (SELECT AVG(ss_ext_discount_amt) FROM store_sales WHERE ss_quantity BETWEEN 1 AND 20)
       ELSE (SELECT AVG(ss_net_paid) FROM store_sales WHERE ss_quantity BETWEEN 1 AND 20) END bucket1,
  CASE WHEN (SELECT COUNT(*) FROM store_sales WHERE ss_quantity BETWEEN 21 AND 40)>122840
       THEN (SELECT AVG(ss_ext_discount_amt) FROM store_sales WHERE ss_quantity BETWEEN 21 AND 40)
       ELSE (SELECT AVG(ss_net_paid) FROM store_sales WHERE ss_quantity BETWEEN 21 AND 40) END bucket2,
  CASE WHEN (SELECT COUNT(*) FROM store_sales WHERE ss_quantity BETWEEN 41 AND 60)>56580
       THEN (SELECT AVG(ss_ext_discount_amt) FROM store_sales WHERE ss_quantity BETWEEN 41 AND 60)
       ELSE (SELECT AVG(ss_net_paid) FROM store_sales WHERE ss_quantity BETWEEN 41 AND 60) END bucket3,
  CASE WHEN (SELECT COUNT(*) FROM store_sales WHERE ss_quantity BETWEEN 61 AND 80)>10097
       THEN (SELECT AVG(ss_ext_discount_amt) FROM store_sales WHERE ss_quantity BETWEEN 61 AND 80)
       ELSE (SELECT AVG(ss_net_paid) FROM store_sales WHERE ss_quantity BETWEEN 61 AND 80) END bucket4,
  CASE WHEN (SELECT COUNT(*) FROM store_sales WHERE ss_quantity BETWEEN 81 AND 100)>15098
       THEN (SELECT AVG(ss_ext_discount_amt) FROM store_sales WHERE ss_quantity BETWEEN 81 AND 100)
       ELSE (SELECT AVG(ss_net_paid) FROM store_sales WHERE ss_quantity BETWEEN 81 AND 100) END bucket5
FROM reason WHERE r_reason_sk=1;
"""},
    {"id": 10, "name": "Customer Segment Purchasing Behavior", "sql": """
SELECT cd_gender, cd_marital_status, cd_education_status,
       COUNT(*) cnt1, AVG(cd_purchase_estimate) avg1, COUNT(*) cnt2, COUNT(*) cnt3
FROM customer c, customer_address ca, customer_demographics cd
WHERE c.c_current_addr_sk=ca.ca_address_sk
  AND ca_county IN ('Rush County','Toole County','Jefferson County','Dona Ana County','La Porte County')
  AND cd_demo_sk=c.c_current_cdemo_sk
  AND EXISTS (SELECT * FROM store_sales,date_dim WHERE c.c_customer_sk=ss_customer_sk
              AND ss_sold_date_sk=d_date_sk AND d_year=2002 AND d_moy BETWEEN 1 AND 4)
  AND (EXISTS (SELECT * FROM web_sales,date_dim WHERE c.c_customer_sk=ws_bill_customer_sk
               AND ws_sold_date_sk=d_date_sk AND d_year=2002 AND d_moy BETWEEN 1 AND 4)
    OR EXISTS (SELECT * FROM catalog_sales,date_dim WHERE c.c_customer_sk=cs_ship_customer_sk
               AND cs_sold_date_sk=d_date_sk AND d_year=2002 AND d_moy BETWEEN 1 AND 4))
GROUP BY cd_gender,cd_marital_status,cd_education_status
ORDER BY cd_gender,cd_marital_status,cd_education_status LIMIT 100;
"""},
    {"id": 11, "name": "Customer YoY Store vs Web Spend", "sql": """
WITH year_total AS (
    SELECT c_customer_id customer_id, c_first_name customer_first_name,
           c_last_name customer_last_name, c_preferred_cust_flag customer_preferred_cust_flag,
           c_birth_country customer_birth_country, c_login customer_login,
           c_email_address customer_email_address, d_year dyear,
           SUM(ss_ext_list_price-ss_ext_discount_amt) year_total, 's' sale_type
    FROM customer, store_sales, date_dim
    WHERE c_customer_sk=ss_customer_sk AND ss_sold_date_sk=d_date_sk
    GROUP BY c_customer_id,c_first_name,c_last_name,c_preferred_cust_flag,
             c_birth_country,c_login,c_email_address,d_year
    UNION ALL
    SELECT c_customer_id,c_first_name,c_last_name,c_preferred_cust_flag,
           c_birth_country,c_login,c_email_address,d_year,
           SUM(ws_ext_list_price-ws_ext_discount_amt), 'w'
    FROM customer, web_sales, date_dim
    WHERE c_customer_sk=ws_bill_customer_sk AND ws_sold_date_sk=d_date_sk
    GROUP BY c_customer_id,c_first_name,c_last_name,c_preferred_cust_flag,
             c_birth_country,c_login,c_email_address,d_year
)
SELECT t_s_secyear.customer_id, t_s_secyear.customer_first_name,
       t_s_secyear.customer_last_name, t_s_secyear.customer_preferred_cust_flag
FROM year_total t_s_firstyear, year_total t_s_secyear,
     year_total t_w_firstyear, year_total t_w_secyear
WHERE t_s_firstyear.customer_id=t_s_secyear.customer_id
  AND t_s_firstyear.customer_id=t_w_firstyear.customer_id
  AND t_s_firstyear.customer_id=t_w_secyear.customer_id
  AND t_s_firstyear.sale_type='s' AND t_w_firstyear.sale_type='w'
  AND t_s_secyear.sale_type='s'   AND t_w_secyear.sale_type='w'
  AND t_s_firstyear.dyear=2001 AND t_s_secyear.dyear=2002
  AND t_w_firstyear.dyear=2001 AND t_w_secyear.dyear=2002
  AND t_s_firstyear.year_total>0 AND t_w_firstyear.year_total>0
  AND CASE WHEN t_w_firstyear.year_total>0 THEN t_w_secyear.year_total/t_w_firstyear.year_total ELSE 0 END
    > CASE WHEN t_s_firstyear.year_total>0 THEN t_s_secyear.year_total/t_s_firstyear.year_total ELSE 0 END
ORDER BY t_s_secyear.customer_id,t_s_secyear.customer_first_name,
         t_s_secyear.customer_last_name,t_s_secyear.customer_preferred_cust_flag LIMIT 100;
"""},
    {"id": 12, "name": "Web Sales Item Revenue", "sql": """
SELECT i_item_id, i_item_desc, i_category, i_class, i_current_price,
       SUM(ws_ext_sales_price) itemrevenue,
       SUM(ws_ext_sales_price)*100/SUM(SUM(ws_ext_sales_price)) OVER (PARTITION BY i_class) revenueratio
FROM web_sales, item, date_dim
WHERE ws_item_sk=i_item_sk AND i_category IN ('Sports','Books','Home')
  AND ws_sold_date_sk=d_date_sk
  AND d_date BETWEEN CAST('1999-02-22' AS DATE) AND CAST('1999-02-22' AS DATE)+INTERVAL'30 days'
GROUP BY i_item_id,i_item_desc,i_category,i_class,i_current_price
ORDER BY i_category,i_class,i_item_id,i_item_desc,revenueratio LIMIT 100;
"""},
    {"id": 13, "name": "Store Sales Demographic Aggregation", "sql": """
SELECT AVG(ss_quantity), AVG(ss_ext_sales_price),
       AVG(ss_ext_wholesale_cost), SUM(ss_ext_wholesale_cost)
FROM store_sales, store, customer_demographics, household_demographics, customer_address, date_dim
WHERE s_store_sk=ss_store_sk AND ss_sold_date_sk=d_date_sk AND d_year=2001
  AND ((ss_hdemo_sk=hd_demo_sk AND cd_demo_sk=ss_cdemo_sk AND cd_marital_status='M'
        AND cd_education_status='Advanced Degree' AND ss_sales_price BETWEEN 100.00 AND 150.00
        AND hd_dep_count=3)
    OR (ss_hdemo_sk=hd_demo_sk AND cd_demo_sk=ss_cdemo_sk AND cd_marital_status='S'
        AND cd_education_status='College' AND ss_sales_price BETWEEN 50.00 AND 100.00
        AND hd_dep_count=1)
    OR (ss_hdemo_sk=hd_demo_sk AND cd_demo_sk=ss_cdemo_sk AND cd_marital_status='W'
        AND cd_education_status='2 yr Degree' AND ss_sales_price BETWEEN 150.00 AND 200.00
        AND hd_dep_count=1))
  AND ((ss_addr_sk=ca_address_sk AND ca_country='United States' AND ca_state IN ('TX','OH','TX') AND ss_net_profit BETWEEN 100 AND 200)
    OR (ss_addr_sk=ca_address_sk AND ca_country='United States' AND ca_state IN ('OR','NM','KY') AND ss_net_profit BETWEEN 150 AND 300)
    OR (ss_addr_sk=ca_address_sk AND ca_country='United States' AND ca_state IN ('VA','TX','MS') AND ss_net_profit BETWEEN 50 AND 250));
"""},
    {"id": 14, "name": "Top Items Sold Across Channels", "sql": """
WITH cross_items AS (
    SELECT i_item_sk ss_item_sk FROM item,
    (SELECT iss.i_brand_id brand_id,iss.i_class_id class_id,iss.i_category_id category_id
     FROM store_sales,item iss,date_dim d1 WHERE ss_item_sk=iss.i_item_sk
       AND ss_sold_date_sk=d1.d_date_sk AND d1.d_year BETWEEN 1999 AND 2001
     INTERSECT
     SELECT ics.i_brand_id,ics.i_class_id,ics.i_category_id
     FROM catalog_sales,item ics,date_dim d2 WHERE cs_item_sk=ics.i_item_sk
       AND cs_sold_date_sk=d2.d_date_sk AND d2.d_year BETWEEN 1999 AND 2001
     INTERSECT
     SELECT iws.i_brand_id,iws.i_class_id,iws.i_category_id
     FROM web_sales,item iws,date_dim d3 WHERE ws_item_sk=iws.i_item_sk
       AND ws_sold_date_sk=d3.d_date_sk AND d3.d_year BETWEEN 1999 AND 2001
    ) x WHERE i_brand_id=brand_id AND i_class_id=class_id AND i_category_id=category_id
),
avg_sales AS (
    SELECT AVG(quantity*list_price) average_sales FROM (
        SELECT ss_quantity quantity,ss_list_price list_price FROM store_sales,date_dim
        WHERE ss_sold_date_sk=d_date_sk AND d_year BETWEEN 1999 AND 2001
        UNION ALL
        SELECT cs_quantity,cs_list_price FROM catalog_sales,date_dim
        WHERE cs_sold_date_sk=d_date_sk AND d_year BETWEEN 1999 AND 2001
        UNION ALL
        SELECT ws_quantity,ws_list_price FROM web_sales,date_dim
        WHERE ws_sold_date_sk=d_date_sk AND d_year BETWEEN 1999 AND 2001
    ) x
)
SELECT channel,i_brand_id,i_class_id,i_category_id,SUM(sales) total_sales,SUM(number_sales) total_number_sales
FROM (
    SELECT 'store' channel,i_brand_id,i_class_id,i_category_id,
           SUM(ss_quantity*ss_list_price) sales,COUNT(*) number_sales
    FROM store_sales,item,date_dim WHERE ss_item_sk=i_item_sk
      AND i_item_sk IN (SELECT ss_item_sk FROM cross_items)
      AND ss_sold_date_sk=d_date_sk AND d_year=2001 AND d_moy=11
    GROUP BY i_brand_id,i_class_id,i_category_id
    HAVING SUM(ss_quantity*ss_list_price)>(SELECT average_sales FROM avg_sales)
    UNION ALL
    SELECT 'catalog',i_brand_id,i_class_id,i_category_id,
           SUM(cs_quantity*cs_list_price),COUNT(*)
    FROM catalog_sales,item,date_dim WHERE cs_item_sk=i_item_sk
      AND i_item_sk IN (SELECT ss_item_sk FROM cross_items)
      AND cs_sold_date_sk=d_date_sk AND d_year=2001 AND d_moy=11
    GROUP BY i_brand_id,i_class_id,i_category_id
    HAVING SUM(cs_quantity*cs_list_price)>(SELECT average_sales FROM avg_sales)
    UNION ALL
    SELECT 'web',i_brand_id,i_class_id,i_category_id,
           SUM(ws_quantity*ws_list_price),COUNT(*)
    FROM web_sales,item,date_dim WHERE ws_item_sk=i_item_sk
      AND i_item_sk IN (SELECT ss_item_sk FROM cross_items)
      AND ws_sold_date_sk=d_date_sk AND d_year=2001 AND d_moy=11
    GROUP BY i_brand_id,i_class_id,i_category_id
    HAVING SUM(ws_quantity*ws_list_price)>(SELECT average_sales FROM avg_sales)
) y
GROUP BY ROLLUP(channel,i_brand_id,i_class_id,i_category_id)
ORDER BY channel,i_brand_id,i_class_id,i_category_id LIMIT 100;
"""},
    {"id": 15, "name": "Catalog Sales by Customer ZIP", "sql": """
SELECT ca_zip, SUM(cs_sales_price)
FROM catalog_sales,customer,customer_address,date_dim
WHERE cs_bill_customer_sk=c_customer_sk AND c_current_addr_sk=ca_address_sk
  AND (SUBSTR(ca_zip,1,5) IN ('85669','86197','88274','83405','86475','85392','85460','80348','81792')
    OR ca_state IN ('CA','WA','GA') OR cs_sales_price>500)
  AND cs_sold_date_sk=d_date_sk AND d_qoy=2 AND d_year=2001
GROUP BY ca_zip ORDER BY ca_zip LIMIT 100;
"""},
    {"id": 16, "name": "Catalog Distinct Orders by Ship Date", "sql": """
SELECT COUNT(DISTINCT cs_order_number) order_count,
       SUM(cs_ext_ship_cost) total_shipping_cost, SUM(cs_net_profit) total_net_profit
FROM catalog_sales cs1,date_dim,customer_address,call_center
WHERE d_date BETWEEN CAST('2002-02-01' AS DATE) AND CAST('2002-02-01' AS DATE)+INTERVAL'60 days'
  AND cs1.cs_ship_date_sk=d_date_sk AND cs1.cs_ship_addr_sk=ca_address_sk
  AND ca_state='GA' AND cs1.cs_call_center_sk=cc_call_center_sk
  AND cc_county IN ('Williamson County','Williamson County','Williamson County','Williamson County','Williamson County')
  AND EXISTS (SELECT * FROM catalog_sales cs2 WHERE cs1.cs_order_number=cs2.cs_order_number AND cs1.cs_warehouse_sk<>cs2.cs_warehouse_sk)
  AND NOT EXISTS (SELECT * FROM catalog_returns cr1 WHERE cs1.cs_order_number=cr1.cr_order_number)
ORDER BY COUNT(DISTINCT cs_order_number) LIMIT 100;
"""},
    {"id": 17, "name": "Store Sales and Returns by Item", "sql": """
SELECT i_item_id, i_item_desc, s_state,
       COUNT(ss_quantity) store_sales_quantitycount,
       AVG(ss_quantity) store_sales_quantityave,
       STDDEV_SAMP(ss_quantity) store_sales_quantitystdev,
       STDDEV_SAMP(ss_quantity)/AVG(ss_quantity) store_sales_quantitycov,
       COUNT(sr_return_quantity) store_returns_quantitycount,
       AVG(sr_return_quantity) store_returns_quantityave,
       STDDEV_SAMP(sr_return_quantity) store_returns_quantitystdev,
       STDDEV_SAMP(sr_return_quantity)/AVG(sr_return_quantity) store_returns_quantitycov,
       COUNT(cs_quantity) catalog_sales_quantitycount,
       AVG(cs_quantity) catalog_sales_quantityave,
       STDDEV_SAMP(cs_quantity)/AVG(cs_quantity) catalog_sales_quantitystdev,
       STDDEV_SAMP(cs_quantity)/AVG(cs_quantity) catalog_sales_quantitycov
FROM store_sales,store_returns,catalog_sales,
     date_dim d1,date_dim d2,date_dim d3,store,item
WHERE d1.d_quarter_name='2001Q1' AND d1.d_date_sk=ss_sold_date_sk
  AND i_item_sk=ss_item_sk AND s_store_sk=ss_store_sk
  AND ss_customer_sk=sr_customer_sk AND ss_item_sk=sr_item_sk
  AND ss_ticket_number=sr_ticket_number
  AND sr_returned_date_sk=d2.d_date_sk AND d2.d_quarter_name IN ('2001Q1','2001Q2','2001Q3')
  AND sr_customer_sk=cs_bill_customer_sk AND sr_item_sk=cs_item_sk
  AND cs_sold_date_sk=d3.d_date_sk AND d3.d_quarter_name IN ('2001Q1','2001Q2','2001Q3')
GROUP BY i_item_id,i_item_desc,s_state ORDER BY i_item_id,i_item_desc,s_state LIMIT 100;
"""},
    {"id": 18, "name": "Catalog Sales by Demographics and Geography", "sql": """
SELECT i_item_id, ca_country, ca_state, ca_county,
       AVG(CAST(cs_quantity AS DECIMAL(12,2))) agg1,
       AVG(CAST(cs_list_price AS DECIMAL(12,2))) agg2,
       AVG(CAST(cs_coupon_amt AS DECIMAL(12,2))) agg3,
       AVG(CAST(cs_sales_price AS DECIMAL(12,2))) agg4,
       AVG(CAST(cs_net_profit AS DECIMAL(12,2))) agg5,
       AVG(CAST(c_birth_year AS DECIMAL(12,2))) agg6,
       AVG(CAST(cd1.cd_dep_count AS DECIMAL(12,2))) agg7
FROM catalog_sales,customer_demographics cd1,customer_demographics cd2,
     customer,customer_address,date_dim,item
WHERE cs_sold_date_sk=d_date_sk AND cs_item_sk=i_item_sk
  AND cs_bill_cdemo_sk=cd1.cd_demo_sk AND cs_bill_customer_sk=c_customer_sk
  AND cd1.cd_gender='F' AND cd1.cd_education_status='Unknown'
  AND c_current_cdemo_sk=cd2.cd_demo_sk AND c_current_addr_sk=ca_address_sk
  AND c_birth_month IN (1,6,8,9,12,2) AND d_year=1998
  AND ca_state IN ('MS','IN','ND','OK','NM','VA','MS')
GROUP BY ROLLUP(i_item_id,ca_country,ca_state,ca_county)
ORDER BY ca_country,ca_state,ca_county,i_item_id LIMIT 100;
"""},
    {"id": 19, "name": "Store Sales by Brand and Manager", "sql": """
SELECT i_brand_id brand_id, i_brand brand, i_manufact_id, i_manufact,
       SUM(ss_ext_sales_price) ext_price
FROM date_dim,store_sales,item,customer,customer_address,store
WHERE d_date_sk=ss_sold_date_sk AND ss_item_sk=i_item_sk AND i_manager_id=7
  AND d_moy=11 AND d_year=1999 AND ss_customer_sk=c_customer_sk
  AND c_current_addr_sk=ca_address_sk
  AND SUBSTR(ca_zip,1,5)<>SUBSTR(s_zip,1,5) AND ss_store_sk=s_store_sk
GROUP BY i_brand,i_brand_id,i_manufact_id,i_manufact
ORDER BY ext_price DESC,i_brand,i_brand_id,i_manufact_id,i_manufact LIMIT 100;
"""},
    {"id": 20, "name": "Catalog Sales by Category and Class", "sql": """
SELECT i_item_id, i_item_desc, i_category, i_class, i_current_price,
       SUM(cs_ext_sales_price) itemrevenue,
       SUM(cs_ext_sales_price)*100/SUM(SUM(cs_ext_sales_price)) OVER (PARTITION BY i_class) revenueratio
FROM catalog_sales,item,date_dim
WHERE cs_item_sk=i_item_sk AND i_category IN ('Sports','Books','Home')
  AND cs_sold_date_sk=d_date_sk
  AND d_date BETWEEN CAST('1999-02-22' AS DATE) AND CAST('1999-02-22' AS DATE)+INTERVAL'30 days'
GROUP BY i_item_id,i_item_desc,i_category,i_class,i_current_price
ORDER BY i_category,i_class,i_item_id,i_item_desc,revenueratio LIMIT 100;
"""},
    {"id": 21, "name": "Inventory Change Analysis", "sql": """
SELECT * FROM (
    SELECT w_warehouse_name, i_item_id,
           SUM(CASE WHEN CAST(d_date AS DATE)<CAST('2000-03-11' AS DATE) THEN inv_quantity_on_hand ELSE 0 END) inv_before,
           SUM(CASE WHEN CAST(d_date AS DATE)>=CAST('2000-03-11' AS DATE) THEN inv_quantity_on_hand ELSE 0 END) inv_after
    FROM inventory,warehouse,item,date_dim
    WHERE i_current_price BETWEEN 0.99 AND 1.49
      AND i_item_sk=inv_item_sk AND inv_warehouse_sk=w_warehouse_sk AND inv_date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-03-11' AS DATE)-INTERVAL'30 days' AND CAST('2000-03-11' AS DATE)+INTERVAL'30 days'
    GROUP BY w_warehouse_name,i_item_id
) x
WHERE (CASE WHEN inv_before>0 THEN inv_after/inv_before ELSE NULL END) BETWEEN 2.0/3.0 AND 3.0/2.0
ORDER BY w_warehouse_name,i_item_id LIMIT 100;
"""},
    {"id": 22, "name": "Inventory by Warehouse and Item", "sql": """
SELECT w_warehouse_name, w_warehouse_sq_ft, w_city, w_county, w_state, w_country,
       'DHL,BARIAN' ship_carriers, d_year year1,
       SUM(CASE WHEN d_moy=1  THEN ws_sales_price*ws_quantity ELSE 0 END) jan_sales,
       SUM(CASE WHEN d_moy=2  THEN ws_sales_price*ws_quantity ELSE 0 END) feb_sales,
       SUM(CASE WHEN d_moy=3  THEN ws_sales_price*ws_quantity ELSE 0 END) mar_sales,
       SUM(CASE WHEN d_moy=4  THEN ws_sales_price*ws_quantity ELSE 0 END) apr_sales,
       SUM(CASE WHEN d_moy=5  THEN ws_sales_price*ws_quantity ELSE 0 END) may_sales,
       SUM(CASE WHEN d_moy=6  THEN ws_sales_price*ws_quantity ELSE 0 END) jun_sales,
       SUM(CASE WHEN d_moy=7  THEN ws_sales_price*ws_quantity ELSE 0 END) jul_sales,
       SUM(CASE WHEN d_moy=8  THEN ws_sales_price*ws_quantity ELSE 0 END) aug_sales,
       SUM(CASE WHEN d_moy=9  THEN ws_sales_price*ws_quantity ELSE 0 END) sep_sales,
       SUM(CASE WHEN d_moy=10 THEN ws_sales_price*ws_quantity ELSE 0 END) oct_sales,
       SUM(CASE WHEN d_moy=11 THEN ws_sales_price*ws_quantity ELSE 0 END) nov_sales,
       SUM(CASE WHEN d_moy=12 THEN ws_sales_price*ws_quantity ELSE 0 END) dec_sales,
       SUM(CASE WHEN d_moy=1  THEN ws_net_paid*ws_quantity ELSE 0 END) jan_net,
       SUM(CASE WHEN d_moy=2  THEN ws_net_paid*ws_quantity ELSE 0 END) feb_net,
       SUM(CASE WHEN d_moy=3  THEN ws_net_paid*ws_quantity ELSE 0 END) mar_net,
       SUM(CASE WHEN d_moy=4  THEN ws_net_paid*ws_quantity ELSE 0 END) apr_net,
       SUM(CASE WHEN d_moy=5  THEN ws_net_paid*ws_quantity ELSE 0 END) may_net,
       SUM(CASE WHEN d_moy=6  THEN ws_net_paid*ws_quantity ELSE 0 END) jun_net,
       SUM(CASE WHEN d_moy=7  THEN ws_net_paid*ws_quantity ELSE 0 END) jul_net,
       SUM(CASE WHEN d_moy=8  THEN ws_net_paid*ws_quantity ELSE 0 END) aug_net,
       SUM(CASE WHEN d_moy=9  THEN ws_net_paid*ws_quantity ELSE 0 END) sep_net,
       SUM(CASE WHEN d_moy=10 THEN ws_net_paid*ws_quantity ELSE 0 END) oct_net,
       SUM(CASE WHEN d_moy=11 THEN ws_net_paid*ws_quantity ELSE 0 END) nov_net,
       SUM(CASE WHEN d_moy=12 THEN ws_net_paid*ws_quantity ELSE 0 END) dec_net
FROM web_sales,warehouse,date_dim,time_dim,ship_mode
WHERE ws_warehouse_sk=w_warehouse_sk AND ws_sold_date_sk=d_date_sk
  AND ws_sold_time_sk=t_time_sk AND ws_ship_mode_sk=sm_ship_mode_sk
  AND d_year=2001 AND t_time BETWEEN 30838 AND 30838+28800
  AND sm_carrier IN ('DHL','BARIAN')
GROUP BY w_warehouse_name,w_warehouse_sq_ft,w_city,w_county,w_state,w_country,d_year
ORDER BY w_warehouse_name LIMIT 100;
"""},
    {"id": 23, "name": "Items Sold by Frequent Buyers", "sql": """
WITH frequent_ss_items AS (
    SELECT SUBSTR(i_item_desc,1,30) itemdesc, i_item_sk item_sk, d_date solddate, COUNT(*) cnt
    FROM store_sales,date_dim,item
    WHERE ss_sold_date_sk=d_date_sk AND ss_item_sk=i_item_sk
      AND d_year IN (2000,2001,2002,2003)
    GROUP BY SUBSTR(i_item_desc,1,30),i_item_sk,d_date HAVING COUNT(*)>4
),
max_store_sales AS (
    SELECT MAX(csales) tpcds_cmax FROM (
        SELECT c_customer_sk,SUM(ss_quantity*ss_sales_price) csales
        FROM store_sales,customer,date_dim
        WHERE ss_customer_sk=c_customer_sk AND ss_sold_date_sk=d_date_sk
          AND d_year IN (2000,2001,2002,2003)
        GROUP BY c_customer_sk) x
),
best_ss_customer AS (
    SELECT c_customer_sk,SUM(ss_quantity*ss_sales_price) ssales
    FROM store_sales,customer WHERE ss_customer_sk=c_customer_sk
    GROUP BY c_customer_sk
    HAVING SUM(ss_quantity*ss_sales_price)>(95/100.0)*(SELECT tpcds_cmax FROM max_store_sales)
)
SELECT SUM(sales) FROM (
    SELECT cs_quantity*cs_list_price sales FROM catalog_sales,date_dim
    WHERE cs_sold_date_sk=d_date_sk AND d_year=2000 AND d_moy=2
      AND cs_item_sk IN (SELECT item_sk FROM frequent_ss_items)
      AND cs_bill_customer_sk IN (SELECT c_customer_sk FROM best_ss_customer)
    UNION ALL
    SELECT ws_quantity*ws_list_price FROM web_sales,date_dim
    WHERE ws_sold_date_sk=d_date_sk AND d_year=2000 AND d_moy=2
      AND ws_item_sk IN (SELECT item_sk FROM frequent_ss_items)
      AND ws_bill_customer_sk IN (SELECT c_customer_sk FROM best_ss_customer)
) y LIMIT 100;
"""},
    {"id": 24, "name": "Store Net Loss by Market", "sql": """
WITH ssales AS (
    SELECT c_last_name,c_first_name,s_store_name,ca_state,s_state,i_color,
           i_current_price,i_manager_id,i_units,i_size,SUM(ss_net_paid) netpaid
    FROM store_sales,store_returns,store,item,customer,customer_address
    WHERE ss_ticket_number=sr_ticket_number AND ss_item_sk=sr_item_sk
      AND ss_customer_sk=c_customer_sk AND ss_item_sk=i_item_sk
      AND ss_store_sk=s_store_sk AND c_current_addr_sk=ca_address_sk
      AND c_birth_country<>UPPER(ca_country) AND s_zip=ca_zip AND s_market_id=8
    GROUP BY c_last_name,c_first_name,s_store_name,ca_state,s_state,
             i_color,i_current_price,i_manager_id,i_units,i_size
)
SELECT c_last_name,c_first_name,s_store_name,SUM(netpaid) paid
FROM ssales WHERE i_color='pale'
GROUP BY c_last_name,c_first_name,s_store_name
HAVING SUM(netpaid)>(SELECT 0.05*AVG(netpaid) FROM ssales)
ORDER BY c_last_name,c_first_name,s_store_name;
"""},
    {"id": 25, "name": "Store and Catalog Mutual Customers", "sql": """
SELECT i_item_id, i_item_desc, s_store_id, s_store_name,
       SUM(ss_net_profit) store_sales_profit,
       SUM(sr_net_loss) store_returns_loss,
       SUM(cs_net_profit) catalog_sales_profit
FROM store_sales,store_returns,catalog_sales,
     date_dim d1,date_dim d2,date_dim d3,store,item
WHERE d1.d_moy=4 AND d1.d_year=2001 AND d1.d_date_sk=ss_sold_date_sk
  AND i_item_sk=ss_item_sk AND s_store_sk=ss_store_sk
  AND ss_customer_sk=sr_customer_sk AND ss_item_sk=sr_item_sk
  AND ss_ticket_number=sr_ticket_number
  AND sr_returned_date_sk=d2.d_date_sk AND d2.d_moy BETWEEN 4 AND 10 AND d2.d_year=2001
  AND sr_customer_sk=cs_bill_customer_sk AND sr_item_sk=cs_item_sk
  AND cs_sold_date_sk=d3.d_date_sk AND d3.d_moy BETWEEN 4 AND 10 AND d3.d_year=2001
GROUP BY i_item_id,i_item_desc,s_store_id,s_store_name
ORDER BY i_item_id,i_item_desc,s_store_id,s_store_name LIMIT 100;
"""},
]
    +
[
    {"id": 26, "name": "Catalog Sales Coupon Demographics", "sql": """
SELECT i_item_id, AVG(cs_quantity) agg1, AVG(cs_list_price) agg2,
       AVG(cs_coupon_amt) agg3, AVG(cs_sales_price) agg4
FROM catalog_sales,customer_demographics,date_dim,item,promotion
WHERE cs_sold_date_sk=d_date_sk AND cs_item_sk=i_item_sk
  AND cs_bill_cdemo_sk=cd_demo_sk AND cs_promo_sk=p_promo_sk
  AND cd_gender='M' AND cd_marital_status='S' AND cd_education_status='College'
  AND (p_channel_email='N' OR p_channel_event='N') AND d_year=2000
GROUP BY i_item_id ORDER BY i_item_id LIMIT 100;
"""},
    {"id": 27, "name": "Store Sales Demographics Rollup", "sql": """
SELECT i_item_id, s_state, GROUPING(s_state) g_state,
       AVG(ss_quantity) agg1, AVG(ss_list_price) agg2,
       AVG(ss_coupon_amt) agg3, AVG(ss_sales_price) agg4
FROM store_sales,customer_demographics,date_dim,store,item
WHERE ss_sold_date_sk=d_date_sk AND ss_item_sk=i_item_sk
  AND ss_store_sk=s_store_sk AND ss_cdemo_sk=cd_demo_sk
  AND cd_gender='F' AND cd_marital_status='W' AND cd_education_status='Primary'
  AND d_year=1998 AND s_state='TN'
GROUP BY ROLLUP(i_item_id,s_state) ORDER BY i_item_id,s_state LIMIT 100;
"""},
    {"id": 28, "name": "Store Sales Price Range Averages", "sql": """
SELECT * FROM
  (SELECT AVG(ss_list_price) B1_LP,COUNT(ss_list_price) B1_CNT,COUNT(DISTINCT ss_list_price) B1_CNTD
   FROM store_sales WHERE ss_quantity BETWEEN 0 AND 5
     AND (ss_list_price BETWEEN 11 AND 21 OR ss_coupon_amt BETWEEN 460 AND 1460 OR ss_wholesale_cost BETWEEN 14 AND 34)) B1,
  (SELECT AVG(ss_list_price) B2_LP,COUNT(ss_list_price) B2_CNT,COUNT(DISTINCT ss_list_price) B2_CNTD
   FROM store_sales WHERE ss_quantity BETWEEN 6 AND 10
     AND (ss_list_price BETWEEN 91 AND 101 OR ss_coupon_amt BETWEEN 1430 AND 2430 OR ss_wholesale_cost BETWEEN 32 AND 52)) B2,
  (SELECT AVG(ss_list_price) B3_LP,COUNT(ss_list_price) B3_CNT,COUNT(DISTINCT ss_list_price) B3_CNTD
   FROM store_sales WHERE ss_quantity BETWEEN 11 AND 15
     AND (ss_list_price BETWEEN 66 AND 76 OR ss_coupon_amt BETWEEN 920 AND 1920 OR ss_wholesale_cost BETWEEN 4 AND 24)) B3,
  (SELECT AVG(ss_list_price) B4_LP,COUNT(ss_list_price) B4_CNT,COUNT(DISTINCT ss_list_price) B4_CNTD
   FROM store_sales WHERE ss_quantity BETWEEN 16 AND 20
     AND (ss_list_price BETWEEN 142 AND 152 OR ss_coupon_amt BETWEEN 3054 AND 4054 OR ss_wholesale_cost BETWEEN 80 AND 100)) B4,
  (SELECT AVG(ss_list_price) B5_LP,COUNT(ss_list_price) B5_CNT,COUNT(DISTINCT ss_list_price) B5_CNTD
   FROM store_sales WHERE ss_quantity BETWEEN 21 AND 25
     AND (ss_list_price BETWEEN 135 AND 145 OR ss_coupon_amt BETWEEN 14180 AND 15180 OR ss_wholesale_cost BETWEEN 38 AND 58)) B5,
  (SELECT AVG(ss_list_price) B6_LP,COUNT(ss_list_price) B6_CNT,COUNT(DISTINCT ss_list_price) B6_CNTD
   FROM store_sales WHERE ss_quantity BETWEEN 26 AND 30
     AND (ss_list_price BETWEEN 28 AND 38 OR ss_coupon_amt BETWEEN 6415 AND 7415 OR ss_wholesale_cost BETWEEN 42 AND 62)) B6
LIMIT 100;
"""},
    {"id": 29, "name": "Store Sales Returns Duration", "sql": """
SELECT i_item_id, i_item_desc, s_store_id, s_store_name,
       SUM(ss_quantity) store_sales_quantity,
       SUM(sr_return_quantity) store_returns_quantity,
       SUM(cs_quantity) catalog_sales_quantity
FROM store_sales,store_returns,catalog_sales,
     date_dim d1,date_dim d2,date_dim d3,store,item
WHERE d1.d_moy=9 AND d1.d_year=1999 AND d1.d_date_sk=ss_sold_date_sk
  AND i_item_sk=ss_item_sk AND s_store_sk=ss_store_sk
  AND ss_customer_sk=sr_customer_sk AND ss_item_sk=sr_item_sk AND ss_ticket_number=sr_ticket_number
  AND sr_returned_date_sk=d2.d_date_sk AND d2.d_moy BETWEEN 9 AND 12 AND d2.d_year=1999
  AND sr_customer_sk=cs_bill_customer_sk AND sr_item_sk=cs_item_sk
  AND cs_sold_date_sk=d3.d_date_sk AND d3.d_year IN (1999,2000,2001)
GROUP BY i_item_id,i_item_desc,s_store_id,s_store_name
ORDER BY i_item_id DESC,i_item_desc DESC,s_store_id DESC,s_store_name DESC LIMIT 100;
"""},
    {"id": 30, "name": "Web Returns by Customer State", "sql": """
WITH customer_total_return AS (
    SELECT wr_returning_customer_sk ctr_customer_sk, ca_state ctr_state,
           SUM(wr_return_amt) ctr_total_return
    FROM web_returns,date_dim,customer_address
    WHERE wr_returned_date_sk=d_date_sk AND d_year=2002 AND wr_returning_addr_sk=ca_address_sk
    GROUP BY wr_returning_customer_sk,ca_state
)
SELECT c_customer_id,c_salutation,c_first_name,c_last_name,c_preferred_cust_flag,
       ca_street_number,ca_street_name,ca_street_type,ca_suite_number,ca_city,ca_county,
       ca_state,ca_zip,ca_country,ca_gmt_offset,ca_location_type,ctr_total_return
FROM customer_total_return ctr1,customer_address,customer
WHERE ctr1.ctr_total_return>(SELECT AVG(ctr_total_return)*1.2 FROM customer_total_return ctr2 WHERE ctr1.ctr_state=ctr2.ctr_state)
  AND ca_address_sk=c_current_addr_sk AND ca_state='GA'
  AND ctr1.ctr_customer_sk=c_customer_sk
ORDER BY c_customer_id,c_salutation,c_first_name,c_last_name,ca_street_number,
         ca_street_name,ca_street_type,ca_suite_number,ca_city,ca_county,ca_state,
         ca_zip,ca_country,ca_gmt_offset,ca_location_type,ctr_total_return LIMIT 100;
"""},
    {"id": 31, "name": "Store and Web Sales by County", "sql": """
WITH ss AS (
    SELECT ca_county,d_qoy,d_year,SUM(ss_ext_sales_price) store_sales
    FROM store_sales,date_dim,customer_address
    WHERE ss_sold_date_sk=d_date_sk AND ss_addr_sk=ca_address_sk
    GROUP BY ca_county,d_qoy,d_year
),
ws AS (
    SELECT ca_county,d_qoy,d_year,SUM(ws_ext_sales_price) web_sales
    FROM web_sales,date_dim,customer_address
    WHERE ws_sold_date_sk=d_date_sk AND ws_bill_addr_sk=ca_address_sk
    GROUP BY ca_county,d_qoy,d_year
)
SELECT ss1.ca_county,ss1.d_year,
       ws2.web_sales/ws1.web_sales web_q1_q2_increase,
       ss2.store_sales/ss1.store_sales store_q1_q2_increase,
       ws3.web_sales/ws2.web_sales web_q2_q3_increase,
       ss3.store_sales/ss2.store_sales store_q2_q3_increase
FROM ss ss1,ss ss2,ss ss3,ws ws1,ws ws2,ws ws3
WHERE ss1.d_qoy=1 AND ss1.d_year=2000 AND ss1.ca_county=ss2.ca_county
  AND ss2.d_qoy=2 AND ss2.d_year=2000 AND ss2.ca_county=ss3.ca_county
  AND ss3.d_qoy=3 AND ss3.d_year=2000 AND ss1.ca_county=ws1.ca_county
  AND ws1.d_qoy=1 AND ws1.d_year=2000 AND ws1.ca_county=ws2.ca_county
  AND ws2.d_qoy=2 AND ws2.d_year=2000 AND ws2.ca_county=ws3.ca_county
  AND ws3.d_qoy=3 AND ws3.d_year=2000
  AND CASE WHEN ws1.web_sales>0 THEN ws2.web_sales/ws1.web_sales ELSE NULL END
    > CASE WHEN ss1.store_sales>0 THEN ss2.store_sales/ss1.store_sales ELSE NULL END
  AND CASE WHEN ws2.web_sales>0 THEN ws3.web_sales/ws2.web_sales ELSE NULL END
    > CASE WHEN ss2.store_sales>0 THEN ss3.store_sales/ss2.store_sales ELSE NULL END
ORDER BY ss1.ca_county;
"""},
    {"id": 32, "name": "Catalog Sales Discounted Items", "sql": """
SELECT SUM(cs_ext_discount_amt) excess_discount_amount
FROM catalog_sales,item,date_dim
WHERE i_manufact_id=977 AND i_item_sk=cs_item_sk
  AND d_date BETWEEN CAST('2000-01-27' AS DATE) AND CAST('2000-01-27' AS DATE)+INTERVAL'90 days'
  AND d_date_sk=cs_sold_date_sk
  AND cs_ext_discount_amt>(SELECT 1.3*AVG(cs_ext_discount_amt) FROM catalog_sales,date_dim
      WHERE cs_item_sk=i_item_sk
        AND d_date BETWEEN CAST('2000-01-27' AS DATE) AND CAST('2000-01-27' AS DATE)+INTERVAL'90 days'
        AND d_date_sk=cs_sold_date_sk) LIMIT 100;
"""},
    {"id": 33, "name": "Sales by Manufacturer Across Channels", "sql": """
WITH ss AS (
    SELECT i_manufact_id,SUM(ss_ext_sales_price) total_sales
    FROM store_sales,date_dim,customer_address,item
    WHERE i_manufact_id IN (SELECT i_manufact_id FROM item WHERE i_category IN ('Electronics'))
      AND ss_item_sk=i_item_sk AND ss_sold_date_sk=d_date_sk AND d_year=1998 AND d_moy=5
      AND ss_addr_sk=ca_address_sk AND ca_gmt_offset=-5
    GROUP BY i_manufact_id
),
cs AS (
    SELECT i_manufact_id,SUM(cs_ext_sales_price) total_sales
    FROM catalog_sales,date_dim,customer_address,item
    WHERE i_manufact_id IN (SELECT i_manufact_id FROM item WHERE i_category IN ('Electronics'))
      AND cs_item_sk=i_item_sk AND cs_sold_date_sk=d_date_sk AND d_year=1998 AND d_moy=5
      AND cs_bill_addr_sk=ca_address_sk AND ca_gmt_offset=-5
    GROUP BY i_manufact_id
),
ws AS (
    SELECT i_manufact_id,SUM(ws_ext_sales_price) total_sales
    FROM web_sales,date_dim,customer_address,item
    WHERE i_manufact_id IN (SELECT i_manufact_id FROM item WHERE i_category IN ('Electronics'))
      AND ws_item_sk=i_item_sk AND ws_sold_date_sk=d_date_sk AND d_year=1998 AND d_moy=5
      AND ws_bill_addr_sk=ca_address_sk AND ca_gmt_offset=-5
    GROUP BY i_manufact_id
)
SELECT i_manufact_id,SUM(total_sales) total_sales
FROM (SELECT * FROM ss UNION ALL SELECT * FROM cs UNION ALL SELECT * FROM ws) tmp1
GROUP BY i_manufact_id ORDER BY total_sales DESC LIMIT 100;
"""},
    {"id": 34, "name": "Store Sales by Household Demographics", "sql": """
SELECT c_last_name,c_first_name,c_salutation,c_preferred_cust_flag,ss_ticket_number,cnt
FROM (
    SELECT ss_ticket_number,ss_customer_sk,COUNT(*) cnt
    FROM store_sales,date_dim,store,household_demographics
    WHERE store_sales.ss_sold_date_sk=date_dim.d_date_sk AND store_sales.ss_store_sk=store.s_store_sk
      AND store_sales.ss_hdemo_sk=household_demographics.hd_demo_sk
      AND (date_dim.d_dom BETWEEN 1 AND 3 OR date_dim.d_dom BETWEEN 25 AND 28)
      AND (household_demographics.hd_buy_potential='>10000' OR household_demographics.hd_buy_potential='Unknown')
      AND household_demographics.hd_vehicle_count>0
      AND (CASE WHEN household_demographics.hd_vehicle_count>0
                THEN household_demographics.hd_dep_count/household_demographics.hd_vehicle_count
                ELSE NULL END)>1.2
      AND date_dim.d_year IN (1999,2000,2001)
      AND store.s_county IN ('Williamson County','Williamson County','Williamson County','Williamson County')
    GROUP BY ss_ticket_number,ss_customer_sk
) dn,customer
WHERE ss_customer_sk=c_customer_sk AND cnt BETWEEN 15 AND 20
ORDER BY c_last_name,c_first_name,c_salutation,c_preferred_cust_flag DESC,ss_ticket_number LIMIT 100;
"""},
    {"id": 35, "name": "Customer Demographics Analysis", "sql": """
SELECT ca_state,cd_gender,cd_marital_status,cd_dep_count,
       COUNT(*) cnt1, MIN(cd_dep_count) min1, MAX(cd_dep_count) max1, AVG(cd_dep_count) avg1,
       cd_dep_employed_count,
       COUNT(*) cnt2, MIN(cd_dep_employed_count) min2, MAX(cd_dep_employed_count) max2, AVG(cd_dep_employed_count) avg2,
       cd_dep_college_count,
       COUNT(*) cnt3, MIN(cd_dep_college_count) min3, MAX(cd_dep_college_count) max3, AVG(cd_dep_college_count) avg3
FROM customer c,customer_address ca,customer_demographics cd
WHERE c.c_current_addr_sk=ca.ca_address_sk AND cd_demo_sk=c.c_current_cdemo_sk
  AND EXISTS (SELECT * FROM store_sales,date_dim WHERE c.c_customer_sk=ss_customer_sk
              AND ss_sold_date_sk=d_date_sk AND d_year=2002 AND d_qoy BETWEEN 1 AND 3)
  AND (EXISTS (SELECT * FROM web_sales,date_dim WHERE c.c_customer_sk=ws_bill_customer_sk
               AND ws_sold_date_sk=d_date_sk AND d_year=2002 AND d_qoy BETWEEN 1 AND 3)
    OR EXISTS (SELECT * FROM catalog_sales,date_dim WHERE c.c_customer_sk=cs_ship_customer_sk
               AND cs_sold_date_sk=d_date_sk AND d_year=2002 AND d_qoy BETWEEN 1 AND 3))
GROUP BY ca_state,cd_gender,cd_marital_status,cd_dep_count,cd_dep_employed_count,cd_dep_college_count
ORDER BY ca_state,cd_gender,cd_marital_status,cd_dep_count,cd_dep_employed_count,cd_dep_college_count LIMIT 100;
"""},
    {"id": 36, "name": "Store Sales Gross Margin by State", "sql": """
SELECT * FROM (
    SELECT SUM(ss_net_profit)/SUM(ss_ext_sales_price) gross_margin,
           i_category, i_class,
           GROUPING(i_category)+GROUPING(i_class) lochierarchy,
           RANK() OVER (
               PARTITION BY GROUPING(i_category)+GROUPING(i_class),
                            CASE WHEN GROUPING(i_class)=0 THEN i_category END
               ORDER BY SUM(ss_net_profit)/SUM(ss_ext_sales_price) ASC
           ) rank_within_parent
    FROM store_sales,date_dim d1,item,store
    WHERE d1.d_year=2001 AND d1.d_date_sk=ss_sold_date_sk
      AND i_item_sk=ss_item_sk AND s_store_sk=ss_store_sk
      AND s_state IN ('TN','TN','TN','TN','TN','TN','TN','TN')
    GROUP BY ROLLUP(i_category,i_class)
) q36
ORDER BY lochierarchy DESC, CASE WHEN lochierarchy=0 THEN i_category END, rank_within_parent LIMIT 100;
"""},
    {"id": 37, "name": "Catalog Sales Inventory Analysis", "sql": """
SELECT i_item_id, i_item_desc, i_current_price
FROM item,inventory,date_dim,catalog_sales
WHERE i_current_price BETWEEN 68 AND 98 AND inv_item_sk=i_item_sk
  AND d_date_sk=inv_date_sk
  AND d_date BETWEEN CAST('2000-02-01' AS DATE) AND CAST('2000-02-01' AS DATE)+INTERVAL'60 days'
  AND i_manufact_id IN (677,940,694,808)
  AND inv_quantity_on_hand BETWEEN 100 AND 500 AND cs_item_sk=i_item_sk
GROUP BY i_item_id,i_item_desc,i_current_price ORDER BY i_item_id LIMIT 100;
"""},
    {"id": 38, "name": "Customers Common to All Channels", "sql": """
SELECT COUNT(*) cnt FROM (
    SELECT DISTINCT c_last_name,c_first_name,d_date
    FROM store_sales,date_dim,customer
    WHERE store_sales.ss_sold_date_sk=date_dim.d_date_sk
      AND store_sales.ss_customer_sk=customer.c_customer_sk
      AND d_month_seq BETWEEN 1200 AND 1211
    INTERSECT
    SELECT DISTINCT c_last_name,c_first_name,d_date
    FROM catalog_sales,date_dim,customer
    WHERE catalog_sales.cs_sold_date_sk=date_dim.d_date_sk
      AND catalog_sales.cs_bill_customer_sk=customer.c_customer_sk
      AND d_month_seq BETWEEN 1200 AND 1211
    INTERSECT
    SELECT DISTINCT c_last_name,c_first_name,d_date
    FROM web_sales,date_dim,customer
    WHERE web_sales.ws_sold_date_sk=date_dim.d_date_sk
      AND web_sales.ws_bill_customer_sk=customer.c_customer_sk
      AND d_month_seq BETWEEN 1200 AND 1211
) hot_cust LIMIT 100;
"""},
    {"id": 39, "name": "Inventory Mean and Variance", "sql": """
WITH inv AS (
    SELECT w_warehouse_name,w_warehouse_sk,i_item_sk,d_moy,
           STDDEV_SAMP(inv_quantity_on_hand) stdev, AVG(inv_quantity_on_hand) mean,
           CASE WHEN AVG(inv_quantity_on_hand)>0
                THEN STDDEV_SAMP(inv_quantity_on_hand)/AVG(inv_quantity_on_hand)
                ELSE NULL END cov
    FROM inventory,item,warehouse,date_dim
    WHERE inv_item_sk=i_item_sk AND inv_warehouse_sk=w_warehouse_sk
      AND inv_date_sk=d_date_sk AND d_year=2001
    GROUP BY w_warehouse_name,w_warehouse_sk,i_item_sk,d_moy
    HAVING CASE WHEN AVG(inv_quantity_on_hand)>0
                THEN STDDEV_SAMP(inv_quantity_on_hand)/AVG(inv_quantity_on_hand)
                ELSE NULL END>1
)
SELECT inv1.w_warehouse_sk,inv1.i_item_sk,inv1.d_moy,inv1.mean,inv1.cov,
       inv2.d_moy,inv2.mean,inv2.cov
FROM inv inv1,inv inv2
WHERE inv1.i_item_sk=inv2.i_item_sk AND inv1.w_warehouse_sk=inv2.w_warehouse_sk
  AND inv1.d_moy=1 AND inv2.d_moy=2
ORDER BY inv1.w_warehouse_sk,inv1.i_item_sk,inv1.d_moy,inv1.mean,inv1.cov,
         inv2.d_moy,inv2.mean,inv2.cov LIMIT 100;
"""},
    {"id": 40, "name": "Catalog Sales Returns Change", "sql": """
SELECT w_state, i_item_id,
       SUM(CASE WHEN CAST(d_date AS DATE)<CAST('2000-03-11' AS DATE)
                THEN cs_sales_price-COALESCE(cr_refunded_cash,0) ELSE 0 END) sales_before,
       SUM(CASE WHEN CAST(d_date AS DATE)>=CAST('2000-03-11' AS DATE)
                THEN cs_sales_price-COALESCE(cr_refunded_cash,0) ELSE 0 END) sales_after
FROM catalog_sales
LEFT OUTER JOIN catalog_returns ON cs_order_number=cr_order_number AND cs_item_sk=cr_item_sk,
     warehouse,item,date_dim
WHERE i_current_price BETWEEN 0.99 AND 1.49 AND i_item_sk=cs_item_sk
  AND cs_warehouse_sk=w_warehouse_sk AND cs_sold_date_sk=d_date_sk
  AND d_date BETWEEN CAST('2000-03-11' AS DATE)-INTERVAL'30 days' AND CAST('2000-03-11' AS DATE)+INTERVAL'30 days'
GROUP BY w_state,i_item_id ORDER BY w_state,i_item_id LIMIT 100;
"""},
    {"id": 41, "name": "Items by Promotion Constraints", "sql": """
SELECT DISTINCT i_product_name FROM item i1
WHERE i_manufact_id BETWEEN 738 AND 778
  AND (SELECT COUNT(*) item_cnt FROM item
       WHERE (i_manufact=i1.i_manufact
              AND ((i_category='Women' AND (i_color='powder' OR i_color='khaki')
                    AND (i_units='Ounce' OR i_units='Oz') AND (i_size='medium' OR i_size='extra large'))
                OR (i_category='Women' AND (i_color='brown' OR i_color='honeydew')
                    AND (i_units='Bunch' OR i_units='Ton') AND (i_size='N/A' OR i_size='small'))
                OR (i_category='Men' AND (i_color='floral' OR i_color='deep')
                    AND (i_units='N/A' OR i_units='Dozen') AND (i_size='petite' OR i_size='large'))
                OR (i_category='Men' AND (i_color='light' OR i_color='cornflower')
                    AND (i_units='Box' OR i_units='Pound') AND (i_size='medium' OR i_size='extra large'))))
          OR (i_manufact=i1.i_manufact
              AND ((i_category='Women' AND (i_color='midnight' OR i_color='snow')
                    AND (i_units='Pallet' OR i_units='Gross') AND (i_size='medium' OR i_size='extra large'))
                OR (i_category='Women' AND (i_color='cyan' OR i_color='papaya')
                    AND (i_units='Cup' OR i_units='Dram') AND (i_size='N/A' OR i_size='small'))
                OR (i_category='Men' AND (i_color='orange' OR i_color='frosted')
                    AND (i_units='Each' OR i_units='Tbl') AND (i_size='petite' OR i_size='large'))
                OR (i_category='Men' AND (i_color='forest' OR i_color='ghost')
                    AND (i_units='Lb' OR i_units='Bundle') AND (i_size='medium' OR i_size='extra large'))))
  )>1
ORDER BY i_product_name LIMIT 100;
"""},
    {"id": 42, "name": "Store Sales by Hour and Category", "sql": """
SELECT d_year, i_category_id, i_category,
       SUM(ss_ext_sales_price) sum_sales
FROM date_dim,store_sales,item
WHERE d_date_sk=ss_sold_date_sk AND ss_item_sk=i_item_sk
  AND i_manager_id=1 AND d_moy=11 AND d_year=2000
GROUP BY d_year,i_category_id,i_category
ORDER BY sum_sales DESC,d_year,i_category_id,i_category LIMIT 100;
"""},
    {"id": 43, "name": "Store Sales by Day Name", "sql": """
SELECT s_store_name, s_store_id,
       SUM(CASE WHEN d_day_name='Sunday'    THEN ss_sales_price ELSE NULL END) sun_sales,
       SUM(CASE WHEN d_day_name='Monday'    THEN ss_sales_price ELSE NULL END) mon_sales,
       SUM(CASE WHEN d_day_name='Tuesday'   THEN ss_sales_price ELSE NULL END) tue_sales,
       SUM(CASE WHEN d_day_name='Wednesday' THEN ss_sales_price ELSE NULL END) wed_sales,
       SUM(CASE WHEN d_day_name='Thursday'  THEN ss_sales_price ELSE NULL END) thu_sales,
       SUM(CASE WHEN d_day_name='Friday'    THEN ss_sales_price ELSE NULL END) fri_sales,
       SUM(CASE WHEN d_day_name='Saturday'  THEN ss_sales_price ELSE NULL END) sat_sales
FROM date_dim,store_sales,store
WHERE d_date_sk=ss_sold_date_sk AND s_store_sk=ss_store_sk
  AND s_gmt_offset=-5 AND d_year=1998
GROUP BY s_store_name,s_store_id
ORDER BY s_store_name,s_store_id,sun_sales,mon_sales,tue_sales,wed_sales,
         thu_sales,fri_sales,sat_sales LIMIT 100;
"""},
    {"id": 44, "name": "Top and Bottom Items by Store Net Profit", "sql": """
SELECT asceding.rnk, i1.i_product_name best_performing, i2.i_product_name worst_performing
FROM (SELECT * FROM (
         SELECT item_sk, RANK() OVER (ORDER BY rank_col ASC) rnk
         FROM (SELECT ss_item_sk item_sk, AVG(ss_net_profit) rank_col
               FROM store_sales ss1 WHERE ss_store_sk=4
                 AND ss_addr_sk NOT IN (SELECT ca_address_sk FROM customer_address WHERE ca_gmt_offset=-5)
               GROUP BY ss_item_sk
               HAVING AVG(ss_net_profit)>0.9*(
                   SELECT AVG(ss_net_profit) FROM store_sales WHERE ss_store_sk=4
                     AND ss_addr_sk IN (SELECT ca_address_sk FROM customer_address WHERE ca_gmt_offset=-5)
                   GROUP BY ss_store_sk)) V1) V11 WHERE rnk<11) asceding,
     (SELECT * FROM (
         SELECT item_sk, RANK() OVER (ORDER BY rank_col DESC) rnk
         FROM (SELECT ss_item_sk item_sk, AVG(ss_net_profit) rank_col
               FROM store_sales ss2 WHERE ss_store_sk=4
                 AND ss_addr_sk NOT IN (SELECT ca_address_sk FROM customer_address WHERE ca_gmt_offset=-5)
               GROUP BY ss_item_sk
               HAVING AVG(ss_net_profit)>0.9*(
                   SELECT AVG(ss_net_profit) FROM store_sales WHERE ss_store_sk=4
                     AND ss_addr_sk IN (SELECT ca_address_sk FROM customer_address WHERE ca_gmt_offset=-5)
                   GROUP BY ss_store_sk)) V2) V21 WHERE rnk<11) descending,
     item i1, item i2
WHERE asceding.rnk=descending.rnk AND i1.i_item_sk=asceding.item_sk AND i2.i_item_sk=descending.item_sk
ORDER BY asceding.rnk LIMIT 100;
"""},
    {"id": 45, "name": "Web Sales by Customer Age and ZIP", "sql": """
SELECT ca_zip, ca_county, SUM(ws_sales_price)
FROM web_sales,customer,customer_address,date_dim,item
WHERE ws_bill_customer_sk=c_customer_sk AND c_current_addr_sk=ca_address_sk
  AND ws_item_sk=i_item_sk
  AND (SUBSTR(ca_zip,1,5) IN ('85669','86197','88274','83405','86475','85392','85460','80348','81792')
    OR i_item_id IN (SELECT i_item_id FROM item WHERE i_item_sk IN (2,3,5,7,11,13,17,19,23,29)))
  AND ws_sold_date_sk=d_date_sk AND d_qoy=2 AND d_year=2001
GROUP BY ca_zip,ca_county ORDER BY ca_zip,ca_county LIMIT 100;
"""},
    {"id": 46, "name": "Store Sales by City and Household", "sql": """
SELECT c_last_name,c_first_name,ca_city,bought_city,ss_ticket_number,amt,profit
FROM (
    SELECT ss_ticket_number,ss_customer_sk,ca_city bought_city,
           SUM(ss_coupon_amt) amt, SUM(ss_net_profit) profit
    FROM store_sales,date_dim,store,household_demographics,customer_address
    WHERE store_sales.ss_sold_date_sk=date_dim.d_date_sk AND store_sales.ss_store_sk=store.s_store_sk
      AND store_sales.ss_hdemo_sk=household_demographics.hd_demo_sk
      AND store_sales.ss_addr_sk=customer_address.ca_address_sk
      AND (household_demographics.hd_dep_count=4 OR household_demographics.hd_vehicle_count=3)
      AND date_dim.d_dow IN (6,0) AND date_dim.d_year IN (1999,2000,2001)
      AND store.s_city IN ('Fairview','Midway','Fairview','Fairview','Fairview')
    GROUP BY ss_ticket_number,ss_customer_sk,ss_addr_sk,ca_city
) dn,customer,customer_address current_addr
WHERE ss_customer_sk=c_customer_sk AND customer.c_current_addr_sk=current_addr.ca_address_sk
  AND current_addr.ca_city<>bought_city
ORDER BY c_last_name,ss_ticket_number LIMIT 100;
"""},
    {"id": 47, "name": "Store Sales Brand Monthly Analysis", "sql": """
WITH v1 AS (
    SELECT i_category,i_brand,s_store_name,s_company_name,d_year,d_moy,
           SUM(ss_sales_price) sum_sales,
           AVG(SUM(ss_sales_price)) OVER (PARTITION BY i_category,i_brand,s_store_name,s_company_name,d_year) avg_monthly_sales,
           RANK() OVER (PARTITION BY i_category,i_brand,s_store_name,s_company_name ORDER BY d_year,d_moy) rn
    FROM item,store_sales,date_dim,store
    WHERE ss_item_sk=i_item_sk AND ss_sold_date_sk=d_date_sk AND ss_store_sk=s_store_sk
      AND d_year IN (1999,2000)
    GROUP BY i_category,i_brand,s_store_name,s_company_name,d_year,d_moy
),
v2 AS (
    SELECT v1.i_category,v1.d_year,v1.d_moy,v1.avg_monthly_sales,v1.sum_sales,
           v1_lag.sum_sales psum, v1_lead.sum_sales nsum
    FROM v1,v1 v1_lag,v1 v1_lead
    WHERE v1.i_category=v1_lag.i_category AND v1.i_category=v1_lead.i_category
      AND v1.i_brand=v1_lag.i_brand AND v1.i_brand=v1_lead.i_brand
      AND v1.s_store_name=v1_lag.s_store_name AND v1.s_store_name=v1_lead.s_store_name
      AND v1.s_company_name=v1_lag.s_company_name AND v1.s_company_name=v1_lead.s_company_name
      AND v1.rn=v1_lag.rn+1 AND v1.rn=v1_lead.rn-1
)
SELECT * FROM v2 WHERE d_year=1999 AND avg_monthly_sales>0
  AND CASE WHEN avg_monthly_sales>0 THEN ABS(sum_sales-avg_monthly_sales)/avg_monthly_sales ELSE NULL END>0.1
ORDER BY sum_sales-avg_monthly_sales,3 LIMIT 100;
"""},
    {"id": 48, "name": "Store Sales by Education and State", "sql": """
SELECT SUM(ss_quantity)
FROM store_sales,store,customer_demographics,customer_address,date_dim
WHERE s_store_sk=ss_store_sk AND ss_sold_date_sk=d_date_sk AND d_year=2000
  AND ((cd_demo_sk=ss_cdemo_sk AND cd_marital_status='M' AND cd_education_status='4 yr Degree'
        AND ss_sales_price BETWEEN 100.00 AND 150.00)
    OR (cd_demo_sk=ss_cdemo_sk AND cd_marital_status='D' AND cd_education_status='2 yr Degree'
        AND ss_sales_price BETWEEN 50.00 AND 100.00)
    OR (cd_demo_sk=ss_cdemo_sk AND cd_marital_status='S' AND cd_education_status='College'
        AND ss_sales_price BETWEEN 150.00 AND 200.00))
  AND ((ss_addr_sk=ca_address_sk AND ca_country='United States' AND ca_state IN ('CO','OH','TX') AND ss_net_profit BETWEEN 0 AND 2000)
    OR (ss_addr_sk=ca_address_sk AND ca_country='United States' AND ca_state IN ('OR','MN','KY') AND ss_net_profit BETWEEN 150 AND 300)
    OR (ss_addr_sk=ca_address_sk AND ca_country='United States' AND ca_state IN ('VA','CA','MS') AND ss_net_profit BETWEEN 50 AND 250))
LIMIT 100;
"""},
    {"id": 49, "name": "Web Catalog Store Returns Analysis", "sql": """
SELECT channel,item,return_ratio,return_rank,currency_rank FROM (
    SELECT 'web' channel, web.item, web.return_ratio, web.return_rank, web.currency_rank
    FROM (SELECT item,return_ratio,currency_ratio,
                 RANK() OVER (ORDER BY return_ratio) return_rank,
                 RANK() OVER (ORDER BY currency_ratio) currency_rank
          FROM (SELECT ws.ws_item_sk item,
                       (CAST(SUM(COALESCE(wr.wr_return_quantity,0)) AS DECIMAL(15,4))/CAST(SUM(COALESCE(ws.ws_quantity,0)) AS DECIMAL(15,4))) return_ratio,
                       (CAST(SUM(COALESCE(wr.wr_return_amt,0)) AS DECIMAL(15,4))/CAST(SUM(COALESCE(ws.ws_net_paid,0)) AS DECIMAL(15,4))) currency_ratio
                FROM web_sales ws LEFT OUTER JOIN web_returns wr ON (ws.ws_order_number=wr.wr_order_number AND ws.ws_item_sk=wr.wr_item_sk),date_dim
                WHERE wr.wr_return_amt>10000 AND ws.ws_net_profit>1 AND ws.ws_net_paid>0 AND ws.ws_quantity>0
                  AND ws_sold_date_sk=d_date_sk AND d_year=2001 AND d_moy=12
                GROUP BY ws.ws_item_sk) in_web) web
    WHERE (web.return_rank<=10 OR web.currency_rank<=10)
    UNION
    SELECT 'catalog', catalog.item, catalog.return_ratio, catalog.return_rank, catalog.currency_rank
    FROM (SELECT item,return_ratio,currency_ratio,
                 RANK() OVER (ORDER BY return_ratio) return_rank,
                 RANK() OVER (ORDER BY currency_ratio) currency_rank
          FROM (SELECT cs.cs_item_sk item,
                       (CAST(SUM(COALESCE(cr.cr_return_quantity,0)) AS DECIMAL(15,4))/CAST(SUM(COALESCE(cs.cs_quantity,0)) AS DECIMAL(15,4))) return_ratio,
                       (CAST(SUM(COALESCE(cr.cr_return_amount,0)) AS DECIMAL(15,4))/CAST(SUM(COALESCE(cs.cs_net_paid,0)) AS DECIMAL(15,4))) currency_ratio
                FROM catalog_sales cs LEFT OUTER JOIN catalog_returns cr ON (cs.cs_order_number=cr.cr_order_number AND cs.cs_item_sk=cr.cr_item_sk),date_dim
                WHERE cr.cr_return_amount>10000 AND cs.cs_net_profit>1 AND cs.cs_net_paid>0 AND cs.cs_quantity>0
                  AND cs_sold_date_sk=d_date_sk AND d_year=2001 AND d_moy=12
                GROUP BY cs.cs_item_sk) in_cat) catalog
    WHERE (catalog.return_rank<=10 OR catalog.currency_rank<=10)
    UNION
    SELECT 'store', store.item, store.return_ratio, store.return_rank, store.currency_rank
    FROM (SELECT item,return_ratio,currency_ratio,
                 RANK() OVER (ORDER BY return_ratio) return_rank,
                 RANK() OVER (ORDER BY currency_ratio) currency_rank
          FROM (SELECT sts.ss_item_sk item,
                       (CAST(SUM(COALESCE(sr.sr_return_quantity,0)) AS DECIMAL(15,4))/CAST(SUM(COALESCE(sts.ss_quantity,0)) AS DECIMAL(15,4))) return_ratio,
                       (CAST(SUM(COALESCE(sr.sr_return_amt,0)) AS DECIMAL(15,4))/CAST(SUM(COALESCE(sts.ss_net_paid,0)) AS DECIMAL(15,4))) currency_ratio
                FROM store_sales sts LEFT OUTER JOIN store_returns sr ON (sts.ss_ticket_number=sr.sr_ticket_number AND sts.ss_item_sk=sr.sr_item_sk),date_dim
                WHERE sr.sr_return_amt>10000 AND sts.ss_net_profit>1 AND sts.ss_net_paid>0 AND sts.ss_quantity>0
                  AND ss_sold_date_sk=d_date_sk AND d_year=2001 AND d_moy=12
                GROUP BY sts.ss_item_sk) in_store) store
    WHERE (store.return_rank<=10 OR store.currency_rank<=10)
) x ORDER BY 1,4,5 LIMIT 100;
"""},
    {"id": 50, "name": "Store Sales Returns by Duration", "sql": """
SELECT s_store_name,s_company_id,s_street_number,s_street_name,s_street_type,
       s_suite_number,s_city,s_county,s_state,s_zip,
       SUM(CASE WHEN sr_returned_date_sk-ss_sold_date_sk<=30 THEN 1 ELSE 0 END) d30,
       SUM(CASE WHEN sr_returned_date_sk-ss_sold_date_sk>30 AND sr_returned_date_sk-ss_sold_date_sk<=60 THEN 1 ELSE 0 END) d31_60,
       SUM(CASE WHEN sr_returned_date_sk-ss_sold_date_sk>60 AND sr_returned_date_sk-ss_sold_date_sk<=90 THEN 1 ELSE 0 END) d61_90,
       SUM(CASE WHEN sr_returned_date_sk-ss_sold_date_sk>90 AND sr_returned_date_sk-ss_sold_date_sk<=120 THEN 1 ELSE 0 END) d91_120,
       SUM(CASE WHEN sr_returned_date_sk-ss_sold_date_sk>120 THEN 1 ELSE 0 END) gt120
FROM store_sales,store_returns,store,date_dim
WHERE d_month_seq BETWEEN 1200 AND 1211 AND ss_sold_date_sk=d_date_sk
  AND ss_customer_sk=sr_customer_sk AND ss_item_sk=sr_item_sk
  AND ss_ticket_number=sr_ticket_number AND ss_store_sk=s_store_sk
GROUP BY s_store_name,s_company_id,s_street_number,s_street_name,s_street_type,
         s_suite_number,s_city,s_county,s_state,s_zip
ORDER BY s_store_name,s_company_id,s_street_number,s_street_name,s_street_type,
         s_suite_number,s_city,s_county,s_state,s_zip LIMIT 100;
"""},
]
    +
[
    {"id": 51, "name": "Web and Store Cumulative Sales", "sql": """
WITH web_v1 AS (
    SELECT ws_item_sk item_sk, d_date,
           SUM(ws_sales_price) sales_price,
           SUM(SUM(ws_sales_price)) OVER (PARTITION BY ws_item_sk ORDER BY d_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) cume_sales
    FROM web_sales,date_dim
    WHERE ws_sold_date_sk=d_date_sk AND d_month_seq BETWEEN 1200 AND 1211 AND ws_item_sk IS NOT NULL
    GROUP BY ws_item_sk,d_date
),
store_v1 AS (
    SELECT ss_item_sk item_sk, d_date,
           SUM(ss_sales_price) sales_price,
           SUM(SUM(ss_sales_price)) OVER (PARTITION BY ss_item_sk ORDER BY d_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) cume_sales
    FROM store_sales,date_dim
    WHERE ss_sold_date_sk=d_date_sk AND d_month_seq BETWEEN 1200 AND 1211 AND ss_item_sk IS NOT NULL
    GROUP BY ss_item_sk,d_date
)
SELECT * FROM (
    SELECT item_sk,d_date,web_sales,store_sales,
           MAX(web_sales)   OVER (PARTITION BY item_sk ORDER BY d_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) web_cumulative,
           MAX(store_sales) OVER (PARTITION BY item_sk ORDER BY d_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) store_cumulative
    FROM (
        SELECT COALESCE(web.item_sk,store.item_sk) item_sk,
               COALESCE(web.d_date,store.d_date) d_date,
               web.cume_sales web_sales, store.cume_sales store_sales
        FROM web_v1 web FULL OUTER JOIN store_v1 store ON (web.item_sk=store.item_sk AND web.d_date=store.d_date)
    ) x
) y
WHERE web_cumulative>store_cumulative ORDER BY item_sk,d_date LIMIT 100;
"""},
    {"id": 52, "name": "Store Sales Holiday Items", "sql": """
SELECT d_year, i_brand_id brand_id, i_brand brand,
       SUM(ss_ext_sales_price) ext_price
FROM date_dim,store_sales,item
WHERE d_date_sk=ss_sold_date_sk AND ss_item_sk=i_item_sk
  AND i_manager_id=1 AND d_moy=11 AND d_year=2000
GROUP BY d_year,i_brand,i_brand_id
ORDER BY d_year,ext_price DESC,brand_id LIMIT 100;
"""},
    {"id": 53, "name": "Store Sales Brand Category Quarter", "sql": """
SELECT * FROM (
    SELECT i_manufact_id, SUM(ss_sales_price) sum_sales,
           AVG(SUM(ss_sales_price)) OVER (PARTITION BY i_manufact_id) avg_quarterly_sales
    FROM item,store_sales,date_dim,store
    WHERE ss_item_sk=i_item_sk AND ss_sold_date_sk=d_date_sk AND ss_store_sk=s_store_sk
      AND d_month_seq IN (1200,1201,1202,1203,1204,1205,1206,1207,1208,1209,1210,1211)
      AND ((i_category IN ('Books','Children','Electronics') AND i_class IN ('personal','portable','reference','self-help') AND i_brand_id IN (1001001,1001002,1001003,1001004))
        OR (i_category IN ('Music','Shoes','Clothing') AND i_class IN ('accessories','classical','fragrances','pants') AND i_brand_id IN (2001001,2001002,2001003,2001004))
        OR (i_category IN ('Women','Music','Men') AND i_class IN ('accessories','classical','fragrances','pants') AND i_brand_id IN (3001001,3001002,3001003,3001004)))
    GROUP BY i_manufact_id,d_qoy
) tmp1
WHERE CASE WHEN avg_quarterly_sales>0 THEN ABS(sum_sales-avg_quarterly_sales)/avg_quarterly_sales ELSE NULL END>0.1
ORDER BY avg_quarterly_sales,sum_sales,i_manufact_id LIMIT 100;
"""},
    {"id": 54, "name": "Market Segment Analysis", "sql": """
WITH my_customers AS (
    SELECT DISTINCT c_customer_sk,c_current_addr_sk
    FROM (
        SELECT cs_sold_date_sk sold_date_sk,cs_bill_customer_sk customer_sk FROM catalog_sales,item
        WHERE cs_item_sk=i_item_sk AND i_category='Women' AND i_class='accessories'
        UNION ALL
        SELECT ws_sold_date_sk,ws_bill_customer_sk FROM web_sales,item
        WHERE ws_item_sk=i_item_sk AND i_category='Women' AND i_class='accessories'
    ) cs_or_ws_sales,customer,date_dim
    WHERE sold_date_sk=d_date_sk AND d_moy=1 AND d_year=1999 AND customer_sk=c_customer_sk
),
my_revenue AS (
    SELECT c_customer_sk, SUM(ss_ext_sales_price) revenue
    FROM my_customers,store_sales,date_dim,customer_address,store
    WHERE c_current_addr_sk=ca_address_sk AND ca_county=s_county AND ca_state=s_state
      AND ss_sold_date_sk=d_date_sk AND c_customer_sk=ss_customer_sk
      AND d_month_seq BETWEEN (SELECT DISTINCT d_month_seq FROM date_dim WHERE d_year=1999 AND d_moy=1)
                          AND (SELECT DISTINCT d_month_seq FROM date_dim WHERE d_year=1999 AND d_moy=1)+3
    GROUP BY c_customer_sk
),
segments AS (SELECT CAST(revenue/50 AS INT) segment FROM my_revenue)
SELECT segment,COUNT(*) num_customers,segment*50 segment_base
FROM segments GROUP BY segment ORDER BY segment,num_customers LIMIT 100;
"""},
    {"id": 55, "name": "Store Sales Manager and Brand", "sql": """
SELECT i_brand_id brand_id, i_brand brand,
       SUM(ss_ext_sales_price) ext_price
FROM date_dim,store_sales,item
WHERE d_date_sk=ss_sold_date_sk AND ss_item_sk=i_item_sk
  AND i_manager_id=36 AND d_moy=12 AND d_year=2001
GROUP BY i_brand,i_brand_id
ORDER BY ext_price DESC,i_brand_id LIMIT 100;
"""},
    {"id": 56, "name": "Multi-Channel Sales by Item Color", "sql": """
WITH ss AS (
    SELECT i_item_id,SUM(ss_ext_sales_price) total_sales
    FROM store_sales,date_dim,customer_address,item
    WHERE i_color IN ('slate','blanched','burnished') AND ss_item_sk=i_item_sk
      AND ss_sold_date_sk=d_date_sk AND d_year=2001 AND d_moy=2
      AND ss_addr_sk=ca_address_sk AND ca_gmt_offset=-5
    GROUP BY i_item_id
),
cs AS (
    SELECT i_item_id,SUM(cs_ext_sales_price) total_sales
    FROM catalog_sales,date_dim,customer_address,item
    WHERE i_color IN ('slate','blanched','burnished') AND cs_item_sk=i_item_sk
      AND cs_sold_date_sk=d_date_sk AND d_year=2001 AND d_moy=2
      AND cs_bill_addr_sk=ca_address_sk AND ca_gmt_offset=-5
    GROUP BY i_item_id
),
ws AS (
    SELECT i_item_id,SUM(ws_ext_sales_price) total_sales
    FROM web_sales,date_dim,customer_address,item
    WHERE i_color IN ('slate','blanched','burnished') AND ws_item_sk=i_item_sk
      AND ws_sold_date_sk=d_date_sk AND d_year=2001 AND d_moy=2
      AND ws_bill_addr_sk=ca_address_sk AND ca_gmt_offset=-5
    GROUP BY i_item_id
)
SELECT i_item_id,SUM(total_sales) total_sales
FROM (SELECT * FROM ss UNION ALL SELECT * FROM cs UNION ALL SELECT * FROM ws) tmp1
GROUP BY i_item_id ORDER BY total_sales DESC,i_item_id LIMIT 100;
"""},
    {"id": 57, "name": "Catalog Sales Brand by Call Center", "sql": """
WITH v1 AS (
    SELECT i_category,i_brand,cc_name,d_year,d_moy,
           SUM(cs_sales_price) sum_sales,
           AVG(SUM(cs_sales_price)) OVER (PARTITION BY i_category,i_brand,cc_name,d_year) avg_monthly_sales,
           RANK() OVER (PARTITION BY i_category,i_brand,cc_name ORDER BY d_year,d_moy) rn
    FROM item,catalog_sales,date_dim,call_center
    WHERE cs_item_sk=i_item_sk AND cs_sold_date_sk=d_date_sk AND cc_call_center_sk=cs_call_center_sk
      AND d_year IN (1999,2000)
    GROUP BY i_category,i_brand,cc_name,d_year,d_moy
),
v2 AS (
    SELECT v1.i_category,v1.d_year,v1.d_moy,v1.avg_monthly_sales,v1.sum_sales,
           v1_lag.sum_sales psum, v1_lead.sum_sales nsum
    FROM v1,v1 v1_lag,v1 v1_lead
    WHERE v1.i_category=v1_lag.i_category AND v1.i_category=v1_lead.i_category
      AND v1.i_brand=v1_lag.i_brand AND v1.i_brand=v1_lead.i_brand
      AND v1.cc_name=v1_lag.cc_name AND v1.cc_name=v1_lead.cc_name
      AND v1.rn=v1_lag.rn+1 AND v1.rn=v1_lead.rn-1
)
SELECT * FROM v2 WHERE d_year=1999 AND avg_monthly_sales>0
  AND CASE WHEN avg_monthly_sales>0 THEN ABS(sum_sales-avg_monthly_sales)/avg_monthly_sales ELSE NULL END>0.1
ORDER BY sum_sales-avg_monthly_sales,avg_monthly_sales LIMIT 100;
"""},
    {"id": 58, "name": "Cross-Channel Sales Comparison", "sql": """
WITH ss_items AS (
    SELECT i_item_id item_id, SUM(ss_ext_sales_price) ss_item_rev
    FROM store_sales,item,date_dim
    WHERE ss_item_sk=i_item_sk AND d_date_sk=ss_sold_date_sk
      AND d_date BETWEEN CAST('2000-01-03' AS DATE) AND CAST('2000-01-03' AS DATE)+INTERVAL'30 days'
    GROUP BY i_item_id
),
cs_items AS (
    SELECT i_item_id item_id, SUM(cs_ext_sales_price) cs_item_rev
    FROM catalog_sales,item,date_dim
    WHERE cs_item_sk=i_item_sk AND d_date_sk=cs_sold_date_sk
      AND d_date BETWEEN CAST('2000-01-03' AS DATE) AND CAST('2000-01-03' AS DATE)+INTERVAL'30 days'
    GROUP BY i_item_id
),
ws_items AS (
    SELECT i_item_id item_id, SUM(ws_ext_sales_price) ws_item_rev
    FROM web_sales,item,date_dim
    WHERE ws_item_sk=i_item_sk AND d_date_sk=ws_sold_date_sk
      AND d_date BETWEEN CAST('2000-01-03' AS DATE) AND CAST('2000-01-03' AS DATE)+INTERVAL'30 days'
    GROUP BY i_item_id
)
SELECT ss_items.item_id,ss_item_rev,
       ss_item_rev/(ss_item_rev+cs_item_rev+ws_item_rev)/3 ratio,
       cs_item_rev,cs_item_rev/(ss_item_rev+cs_item_rev+ws_item_rev)/3 ratio2,
       ws_item_rev,ws_item_rev/(ss_item_rev+cs_item_rev+ws_item_rev)/3 ratio3
FROM ss_items,cs_items,ws_items
WHERE ss_items.item_id=cs_items.item_id AND ss_items.item_id=ws_items.item_id
  AND ss_item_rev BETWEEN 0.9*cs_item_rev AND 1.1*cs_item_rev
  AND ss_item_rev BETWEEN 0.9*ws_item_rev AND 1.1*ws_item_rev
  AND cs_item_rev BETWEEN 0.9*ss_item_rev AND 1.1*ss_item_rev
  AND cs_item_rev BETWEEN 0.9*ws_item_rev AND 1.1*ws_item_rev
  AND ws_item_rev BETWEEN 0.9*ss_item_rev AND 1.1*ss_item_rev
  AND ws_item_rev BETWEEN 0.9*cs_item_rev AND 1.1*cs_item_rev
ORDER BY item_id,ss_item_rev,cs_item_rev,ws_item_rev LIMIT 100;
"""},
    {"id": 59, "name": "Store Sales by Week", "sql": """
WITH wss AS (
    SELECT d_week_seq, ss_store_sk,
           SUM(CASE WHEN d_day_name='Sunday'    THEN ss_sales_price ELSE NULL END) sun_sales,
           SUM(CASE WHEN d_day_name='Monday'    THEN ss_sales_price ELSE NULL END) mon_sales,
           SUM(CASE WHEN d_day_name='Tuesday'   THEN ss_sales_price ELSE NULL END) tue_sales,
           SUM(CASE WHEN d_day_name='Wednesday' THEN ss_sales_price ELSE NULL END) wed_sales,
           SUM(CASE WHEN d_day_name='Thursday'  THEN ss_sales_price ELSE NULL END) thu_sales,
           SUM(CASE WHEN d_day_name='Friday'    THEN ss_sales_price ELSE NULL END) fri_sales,
           SUM(CASE WHEN d_day_name='Saturday'  THEN ss_sales_price ELSE NULL END) sat_sales
    FROM store_sales,date_dim WHERE d_date_sk=ss_sold_date_sk GROUP BY d_week_seq,ss_store_sk
)
SELECT s1.s_store_name,s1.s_store_id,s1.d_week_seq,
       ROUND(s1.sun_sales/s2.sun_sales,2) sun_sales,
       ROUND(s1.mon_sales/s2.mon_sales,2) mon_sales,
       ROUND(s1.tue_sales/s2.tue_sales,2) tue_sales,
       ROUND(s1.wed_sales/s2.wed_sales,2) wed_sales,
       ROUND(s1.thu_sales/s2.thu_sales,2) thu_sales,
       ROUND(s1.fri_sales/s2.fri_sales,2) fri_sales,
       ROUND(s1.sat_sales/s2.sat_sales,2) sat_sales
FROM (SELECT s_store_id,s_store_name,wss.d_week_seq,sun_sales,mon_sales,tue_sales,wed_sales,thu_sales,fri_sales,sat_sales
      FROM wss,store,date_dim d WHERE d.d_week_seq=wss.d_week_seq AND ss_store_sk=s_store_sk AND d_month_seq BETWEEN 1195 AND 1206) s1,
     (SELECT s_store_id,s_store_name,wss.d_week_seq,sun_sales,mon_sales,tue_sales,wed_sales,thu_sales,fri_sales,sat_sales
      FROM wss,store,date_dim d WHERE d.d_week_seq=wss.d_week_seq AND ss_store_sk=s_store_sk AND d_month_seq BETWEEN 1207 AND 1218) s2
WHERE s1.s_store_name=s2.s_store_name AND s1.s_store_id=s2.s_store_id
  AND (s1.d_week_seq-s2.d_week_seq)=52
ORDER BY s1.s_store_name,s1.s_store_id,s1.d_week_seq LIMIT 100;
"""},
    {"id": 60, "name": "Multi-Channel Sales from Specific States", "sql": """
WITH ss AS (
    SELECT i_item_id,SUM(ss_ext_sales_price) total_sales
    FROM store_sales,date_dim,customer_address,item
    WHERE i_item_id IN (SELECT i_item_id FROM item WHERE i_category IN ('Music'))
      AND ss_item_sk=i_item_sk AND ss_sold_date_sk=d_date_sk AND d_year=1998 AND d_moy=9
      AND ss_addr_sk=ca_address_sk AND ca_gmt_offset=-5
    GROUP BY i_item_id
),
cs AS (
    SELECT i_item_id,SUM(cs_ext_sales_price) total_sales
    FROM catalog_sales,date_dim,customer_address,item
    WHERE i_item_id IN (SELECT i_item_id FROM item WHERE i_category IN ('Music'))
      AND cs_item_sk=i_item_sk AND cs_sold_date_sk=d_date_sk AND d_year=1998 AND d_moy=9
      AND cs_bill_addr_sk=ca_address_sk AND ca_gmt_offset=-5
    GROUP BY i_item_id
),
ws AS (
    SELECT i_item_id,SUM(ws_ext_sales_price) total_sales
    FROM web_sales,date_dim,customer_address,item
    WHERE i_item_id IN (SELECT i_item_id FROM item WHERE i_category IN ('Music'))
      AND ws_item_sk=i_item_sk AND ws_sold_date_sk=d_date_sk AND d_year=1998 AND d_moy=9
      AND ws_bill_addr_sk=ca_address_sk AND ca_gmt_offset=-5
    GROUP BY i_item_id
)
SELECT i_item_id,SUM(total_sales) total_sales
FROM (SELECT * FROM ss UNION ALL SELECT * FROM cs UNION ALL SELECT * FROM ws) tmp1
GROUP BY i_item_id ORDER BY i_item_id,total_sales DESC LIMIT 100;
"""},
    {"id": 61, "name": "Promotion Sales Analysis", "sql": """
SELECT promotional_sales, all_sales,
       CAST(promotional_sales AS DECIMAL(15,4))/CAST(all_sales AS DECIMAL(15,4))*100 promo_percent
FROM (
    SELECT SUM(CASE WHEN p_channel_dmail='Y' OR p_channel_email='Y' OR p_channel_tv='Y'
                    THEN ss_ext_sales_price ELSE 0 END) promotional_sales,
           SUM(ss_ext_sales_price) all_sales
    FROM store_sales,store,promotion,date_dim,customer,customer_address,item
    WHERE ss_sold_date_sk=d_date_sk AND ss_store_sk=s_store_sk AND ss_promo_sk=p_promo_sk
      AND ss_customer_sk=c_customer_sk AND ca_address_sk=c_current_addr_sk
      AND ss_item_sk=i_item_sk AND ca_gmt_offset=-7
      AND i_category='Jewelry' AND d_year=1998 AND d_moy=11
) x LIMIT 100;
"""},
    {"id": 62, "name": "Web Sales Shipping Analysis", "sql": """
SELECT SUBSTR(w_warehouse_name,1,20), sm_type, web_name,
       SUM(CASE WHEN ws_ship_date_sk-ws_sold_date_sk<=30 THEN 1 ELSE 0 END) d30,
       SUM(CASE WHEN ws_ship_date_sk-ws_sold_date_sk>30 AND ws_ship_date_sk-ws_sold_date_sk<=60 THEN 1 ELSE 0 END) d31_60,
       SUM(CASE WHEN ws_ship_date_sk-ws_sold_date_sk>60 AND ws_ship_date_sk-ws_sold_date_sk<=90 THEN 1 ELSE 0 END) d61_90,
       SUM(CASE WHEN ws_ship_date_sk-ws_sold_date_sk>90 AND ws_ship_date_sk-ws_sold_date_sk<=120 THEN 1 ELSE 0 END) d91_120,
       SUM(CASE WHEN ws_ship_date_sk-ws_sold_date_sk>120 THEN 1 ELSE 0 END) gt120
FROM web_sales,warehouse,ship_mode,web_site,date_dim
WHERE d_month_seq BETWEEN 1200 AND 1211 AND ws_ship_date_sk=d_date_sk
  AND ws_warehouse_sk=w_warehouse_sk AND ws_ship_mode_sk=sm_ship_mode_sk AND ws_web_site_sk=web_site_sk
GROUP BY SUBSTR(w_warehouse_name,1,20),sm_type,web_name
ORDER BY SUBSTR(w_warehouse_name,1,20),sm_type,web_name LIMIT 100;
"""},
    {"id": 63, "name": "Store Sales Manager Category Analysis", "sql": """
SELECT * FROM (
    SELECT i_manager_id, SUM(ss_sales_price) sum_sales,
           AVG(SUM(ss_sales_price)) OVER (PARTITION BY i_manager_id) avg_monthly_sales
    FROM item,store_sales,date_dim,store
    WHERE ss_item_sk=i_item_sk AND ss_sold_date_sk=d_date_sk AND ss_store_sk=s_store_sk
      AND d_month_seq IN (1200,1201,1202,1203,1204,1205,1206,1207,1208,1209,1210,1211)
      AND ((i_category IN ('Books','Children','Electronics') AND i_class IN ('personal','portable','reference','self-help') AND i_brand_id IN (1001001,1001002,1001003,1001004))
        OR (i_category IN ('Music','Shoes','Clothing') AND i_class IN ('accessories','classical','fragrances','pants') AND i_brand_id IN (2001001,2001002,2001003,2001004))
        OR (i_category IN ('Women','Music','Men') AND i_class IN ('accessories','classical','fragrances','pants') AND i_brand_id IN (3001001,3001002,3001003,3001004)))
    GROUP BY i_manager_id,d_moy
) tmp1
WHERE CASE WHEN avg_monthly_sales>0 THEN ABS(sum_sales-avg_monthly_sales)/avg_monthly_sales ELSE NULL END>0.1
ORDER BY i_manager_id,avg_monthly_sales,sum_sales LIMIT 100;
"""},
    {"id": 64, "name": "Store Catalog Cross-Channel Customer Analysis", "sql": """
SELECT c_last_name,c_first_name,c_customer_id
FROM store_sales s1,store_sales s2,store_returns,store,
     customer_demographics cd1,customer_demographics cd2,customer,
     date_dim syear,date_dim syyear,date_dim sr_date,
     household_demographics hd1,household_demographics hd2,
     customer_address ad1,customer_address ad2,income_band ib1,income_band ib2,
     item i1,item i2
WHERE s1.ss_store_sk=store.s_store_sk AND store.s_market_id=7
  AND s1.ss_item_sk=i1.i_item_sk AND s2.ss_item_sk=i2.i_item_sk
  AND s1.ss_customer_sk=s2.ss_customer_sk AND s1.ss_ticket_number=s2.ss_ticket_number
  AND s2.ss_item_sk=sr_item_sk AND s2.ss_ticket_number=sr_ticket_number
  AND sr_customer_sk=c_customer_sk
  AND i1.i_color IN ('maroon','burnished','dim')
  AND i2.i_color IN ('maroon','burnished','dim')
  AND i1.i_category='Women' AND i2.i_category=i1.i_category
  AND i1.i_manufact_id=i2.i_manufact_id
  AND s1.ss_cdemo_sk=cd1.cd_demo_sk AND cd1.cd_marital_status='D'
  AND cd1.cd_education_status='Unknown'
  AND s1.ss_hdemo_sk=hd1.hd_demo_sk AND s1.ss_addr_sk=ad1.ca_address_sk
  AND ad1.ca_country='United States' AND hd1.hd_income_band_sk=ib1.ib_income_band_sk
  AND ib1.ib_lower_bound>=15045 AND ib1.ib_upper_bound<=65045
  AND s2.ss_cdemo_sk=cd2.cd_demo_sk AND cd2.cd_marital_status=cd1.cd_marital_status
  AND cd2.cd_education_status=cd1.cd_education_status
  AND s2.ss_hdemo_sk=hd2.hd_demo_sk AND s2.ss_addr_sk=ad2.ca_address_sk
  AND ad2.ca_country=ad1.ca_country AND hd2.hd_income_band_sk=ib2.ib_income_band_sk
  AND ib2.ib_lower_bound=ib1.ib_lower_bound
  AND s1.ss_sold_date_sk=syear.d_date_sk AND syear.d_year=1999
  AND s2.ss_sold_date_sk=syyear.d_date_sk AND syyear.d_year=1999
  AND sr_returned_date_sk=sr_date.d_date_sk AND sr_date.d_year=2000
ORDER BY c_last_name,c_first_name,c_customer_id LIMIT 100;
"""},
    {"id": 65, "name": "Store Sales Item Comparison", "sql": """
SELECT s_store_name,i_item_desc,sc.revenue,i_current_price,i_wholesale_cost,i_brand
FROM store,item,
     (SELECT ss_store_sk,AVG(revenue) ave FROM (
          SELECT ss_store_sk,ss_item_sk,SUM(ss_sales_price) revenue
          FROM store_sales,date_dim WHERE ss_sold_date_sk=d_date_sk AND d_month_seq BETWEEN 1200 AND 1211
          GROUP BY ss_store_sk,ss_item_sk) sa GROUP BY ss_store_sk) sb,
     (SELECT ss_store_sk,ss_item_sk,SUM(ss_sales_price) revenue
      FROM store_sales,date_dim WHERE ss_sold_date_sk=d_date_sk AND d_month_seq BETWEEN 1200 AND 1211
      GROUP BY ss_store_sk,ss_item_sk) sc
WHERE sb.ss_store_sk=sc.ss_store_sk AND sc.revenue<=0.1*sb.ave
  AND s_store_sk=sc.ss_store_sk AND i_item_sk=sc.ss_item_sk
ORDER BY s_store_name,i_item_desc LIMIT 100;
"""},
    {"id": 66, "name": "Warehouse Sales Statistics", "sql": """
SELECT w_warehouse_name,w_warehouse_sq_ft,w_city,w_county,w_state,w_country,
       'DHL,BARIAN' ship_carriers, d_year year1,
       SUM(CASE WHEN d_moy=1  THEN cs_sales_price*cs_quantity ELSE 0 END) jan_sales,
       SUM(CASE WHEN d_moy=2  THEN cs_sales_price*cs_quantity ELSE 0 END) feb_sales,
       SUM(CASE WHEN d_moy=3  THEN cs_sales_price*cs_quantity ELSE 0 END) mar_sales,
       SUM(CASE WHEN d_moy=4  THEN cs_sales_price*cs_quantity ELSE 0 END) apr_sales,
       SUM(CASE WHEN d_moy=5  THEN cs_sales_price*cs_quantity ELSE 0 END) may_sales,
       SUM(CASE WHEN d_moy=6  THEN cs_sales_price*cs_quantity ELSE 0 END) jun_sales,
       SUM(CASE WHEN d_moy=7  THEN cs_sales_price*cs_quantity ELSE 0 END) jul_sales,
       SUM(CASE WHEN d_moy=8  THEN cs_sales_price*cs_quantity ELSE 0 END) aug_sales,
       SUM(CASE WHEN d_moy=9  THEN cs_sales_price*cs_quantity ELSE 0 END) sep_sales,
       SUM(CASE WHEN d_moy=10 THEN cs_sales_price*cs_quantity ELSE 0 END) oct_sales,
       SUM(CASE WHEN d_moy=11 THEN cs_sales_price*cs_quantity ELSE 0 END) nov_sales,
       SUM(CASE WHEN d_moy=12 THEN cs_sales_price*cs_quantity ELSE 0 END) dec_sales,
       SUM(CASE WHEN d_moy=1  THEN cs_net_paid_inc_ship*cs_quantity ELSE 0 END) jan_net,
       SUM(CASE WHEN d_moy=2  THEN cs_net_paid_inc_ship*cs_quantity ELSE 0 END) feb_net,
       SUM(CASE WHEN d_moy=3  THEN cs_net_paid_inc_ship*cs_quantity ELSE 0 END) mar_net,
       SUM(CASE WHEN d_moy=4  THEN cs_net_paid_inc_ship*cs_quantity ELSE 0 END) apr_net,
       SUM(CASE WHEN d_moy=5  THEN cs_net_paid_inc_ship*cs_quantity ELSE 0 END) may_net,
       SUM(CASE WHEN d_moy=6  THEN cs_net_paid_inc_ship*cs_quantity ELSE 0 END) jun_net,
       SUM(CASE WHEN d_moy=7  THEN cs_net_paid_inc_ship*cs_quantity ELSE 0 END) jul_net,
       SUM(CASE WHEN d_moy=8  THEN cs_net_paid_inc_ship*cs_quantity ELSE 0 END) aug_net,
       SUM(CASE WHEN d_moy=9  THEN cs_net_paid_inc_ship*cs_quantity ELSE 0 END) sep_net,
       SUM(CASE WHEN d_moy=10 THEN cs_net_paid_inc_ship*cs_quantity ELSE 0 END) oct_net,
       SUM(CASE WHEN d_moy=11 THEN cs_net_paid_inc_ship*cs_quantity ELSE 0 END) nov_net,
       SUM(CASE WHEN d_moy=12 THEN cs_net_paid_inc_ship*cs_quantity ELSE 0 END) dec_net
FROM catalog_sales,warehouse,date_dim,time_dim,ship_mode
WHERE cs_warehouse_sk=w_warehouse_sk AND cs_sold_date_sk=d_date_sk
  AND cs_sold_time_sk=t_time_sk AND cs_ship_mode_sk=sm_ship_mode_sk
  AND d_year=2001 AND t_time BETWEEN 30838 AND 59638 AND sm_carrier IN ('DHL','BARIAN')
GROUP BY w_warehouse_name,w_warehouse_sq_ft,w_city,w_county,w_state,w_country,d_year
ORDER BY w_warehouse_name LIMIT 100;
"""},
    {"id": 67, "name": "Store Sales by Category Quarter Rollup", "sql": """
SELECT * FROM (
    SELECT i_category,i_class,i_brand,i_product_name,d_year,d_qoy,d_moy,s_store_id,
           sumsales,
           RANK() OVER (PARTITION BY i_category ORDER BY sumsales DESC) rk
    FROM (
        SELECT i_category,i_class,i_brand,i_product_name,d_year,d_qoy,d_moy,s_store_id,
               SUM(COALESCE(ss_sales_price*ss_quantity,0)) sumsales
        FROM store_sales,date_dim,store,item
        WHERE ss_sold_date_sk=d_date_sk AND ss_item_sk=i_item_sk AND ss_store_sk=s_store_sk
          AND d_month_seq BETWEEN 1200 AND 1211
        GROUP BY ROLLUP(i_category,i_class,i_brand,i_product_name,d_year,d_qoy,d_moy,s_store_id)
    ) dw1
) dw2 WHERE rk<=100
ORDER BY i_category,i_class,i_brand,i_product_name,d_year,d_qoy,d_moy,s_store_id,sumsales,rk LIMIT 100;
"""},
    {"id": 68, "name": "Store Sales City County Household", "sql": """
SELECT c_last_name,c_first_name,ca_city,bought_city,ss_ticket_number,extended_price,extended_tax,list_price
FROM (
    SELECT ss_ticket_number,ss_customer_sk,ca_city bought_city,
           SUM(ss_ext_sales_price) extended_price,
           SUM(ss_ext_list_price) list_price,
           SUM(ss_ext_tax) extended_tax
    FROM store_sales,date_dim,store,household_demographics,customer_address
    WHERE store_sales.ss_sold_date_sk=date_dim.d_date_sk AND store_sales.ss_store_sk=store.s_store_sk
      AND store_sales.ss_hdemo_sk=household_demographics.hd_demo_sk
      AND store_sales.ss_addr_sk=customer_address.ca_address_sk
      AND date_dim.d_dom BETWEEN 1 AND 2
      AND (household_demographics.hd_dep_count=4 OR household_demographics.hd_vehicle_count=3)
      AND date_dim.d_year IN (1999,2000,2001)
      AND store.s_city IN ('Midway','Fairview')
    GROUP BY ss_ticket_number,ss_customer_sk,ss_addr_sk,ca_city
) dn,customer,customer_address current_addr
WHERE ss_customer_sk=c_customer_sk AND customer.c_current_addr_sk=current_addr.ca_address_sk
  AND current_addr.ca_city<>bought_city
ORDER BY c_last_name,ss_ticket_number LIMIT 100;
"""},
    {"id": 69, "name": "Customer Segment Web Catalog Overlap", "sql": """
SELECT cd_gender,cd_marital_status,cd_education_status,COUNT(*) cnt1,
       cd_purchase_estimate,COUNT(*) cnt2,cd_credit_rating,COUNT(*) cnt3
FROM customer c,customer_address ca,customer_demographics cd
WHERE c.c_current_addr_sk=ca.ca_address_sk AND ca_state IN ('KY','GA','NM')
  AND cd_demo_sk=c.c_current_cdemo_sk
  AND EXISTS (SELECT * FROM store_sales,date_dim WHERE c.c_customer_sk=ss_customer_sk
              AND ss_sold_date_sk=d_date_sk AND d_year=2002 AND d_moy BETWEEN 1 AND 3)
  AND NOT EXISTS (SELECT * FROM web_sales,date_dim WHERE c.c_customer_sk=ws_bill_customer_sk
                  AND ws_sold_date_sk=d_date_sk AND d_year=2002 AND d_moy BETWEEN 1 AND 3)
  AND NOT EXISTS (SELECT * FROM catalog_sales,date_dim WHERE c.c_customer_sk=cs_ship_customer_sk
                  AND cs_sold_date_sk=d_date_sk AND d_year=2002 AND d_moy BETWEEN 1 AND 3)
GROUP BY cd_gender,cd_marital_status,cd_education_status,cd_purchase_estimate,cd_credit_rating
ORDER BY cd_gender,cd_marital_status,cd_education_status,cd_purchase_estimate,cd_credit_rating LIMIT 100;
"""},
    {"id": 70, "name": "Store Sales State Ranking", "sql": """
SELECT * FROM (
    SELECT SUM(ss_net_profit) total_sum, s_state, s_county,
           GROUPING(s_state)+GROUPING(s_county) lochierarchy,
           RANK() OVER (
               PARTITION BY GROUPING(s_state)+GROUPING(s_county),
                            CASE WHEN GROUPING(s_county)=0 THEN s_state END
               ORDER BY SUM(ss_net_profit) DESC
           ) rank_within_parent
    FROM store_sales,date_dim d1,store
    WHERE d1.d_month_seq BETWEEN 1200 AND 1211 AND d1.d_date_sk=ss_sold_date_sk AND s_store_sk=ss_store_sk
      AND s_state IN (SELECT s_state FROM (
          SELECT s_state,RANK() OVER (PARTITION BY s_state ORDER BY SUM(ss_net_profit) DESC) ranking
          FROM store_sales,store,date_dim WHERE d_month_seq BETWEEN 1200 AND 1211
            AND d_date_sk=ss_sold_date_sk AND s_store_sk=ss_store_sk GROUP BY s_state) tmp1 WHERE ranking<=5)
    GROUP BY ROLLUP(s_state,s_county)
) q70
ORDER BY lochierarchy DESC, CASE WHEN lochierarchy=0 THEN s_state END, rank_within_parent LIMIT 100;
"""},
    {"id": 71, "name": "Sales by Channel and Hour", "sql": """
SELECT i_brand_id brand_id, i_brand brand, t_hour, t_minute,
       SUM(ext_price) ext_price
FROM item,
     (SELECT ws_ext_sales_price ext_price,ws_sold_time_sk sold_time_sk,ws_item_sk item_sk
      FROM web_sales,date_dim WHERE d_date_sk=ws_sold_date_sk AND d_moy=11 AND d_year=2001
      UNION ALL
      SELECT cs_ext_sales_price,cs_sold_time_sk,cs_item_sk
      FROM catalog_sales,date_dim WHERE d_date_sk=cs_sold_date_sk AND d_moy=11 AND d_year=2001
      UNION ALL
      SELECT ss_ext_sales_price,ss_sold_time_sk,ss_item_sk
      FROM store_sales,date_dim WHERE d_date_sk=ss_sold_date_sk AND d_moy=11 AND d_year=2001
     ) tmp,time_dim
WHERE sold_time_sk=t_time_sk AND i_item_sk=item_sk AND i_manager_id=1
GROUP BY i_brand,i_brand_id,t_hour,t_minute
ORDER BY ext_price DESC,i_brand_id LIMIT 100;
"""},
    {"id": 72, "name": "Catalog Sales Inventory Shortage", "sql": """
SELECT i_item_desc,w_warehouse_name,d1.d_week_seq,
       SUM(CASE WHEN p_promo_sk IS NULL THEN 1 ELSE 0 END) no_promo,
       SUM(CASE WHEN p_promo_sk IS NOT NULL THEN 1 ELSE 0 END) promo,
       COUNT(*) total_cnt
FROM catalog_sales
JOIN inventory ON cs_item_sk=inv_item_sk
JOIN warehouse ON w_warehouse_sk=inv_warehouse_sk
JOIN item ON i_item_sk=cs_item_sk
JOIN customer_demographics ON cs_bill_cdemo_sk=cd_demo_sk
JOIN household_demographics ON cs_bill_hdemo_sk=hd_demo_sk
JOIN date_dim d1 ON cs_sold_date_sk=d1.d_date_sk
JOIN date_dim d2 ON inv_date_sk=d2.d_date_sk
JOIN date_dim d3 ON cs_ship_date_sk=d3.d_date_sk
LEFT JOIN promotion ON cs_promo_sk=p_promo_sk
WHERE d1.d_week_seq=d2.d_week_seq AND i_current_price>1.49
  AND d3.d_date>d1.d_date+5 AND hd_buy_potential='>10000'
  AND d1.d_year=2001 AND cd_marital_status='M'
GROUP BY i_item_desc,w_warehouse_name,d1.d_week_seq
ORDER BY total_cnt DESC,i_item_desc,w_warehouse_name,d1.d_week_seq LIMIT 100;
"""},
    {"id": 73, "name": "Store Sales Household Count", "sql": """
SELECT c_last_name,c_first_name,c_salutation,c_preferred_cust_flag,ss_ticket_number,cnt
FROM (
    SELECT ss_ticket_number,ss_customer_sk,COUNT(*) cnt
    FROM store_sales,date_dim,store,household_demographics
    WHERE store_sales.ss_sold_date_sk=date_dim.d_date_sk AND store_sales.ss_store_sk=store.s_store_sk
      AND store_sales.ss_hdemo_sk=household_demographics.hd_demo_sk
      AND (date_dim.d_dom BETWEEN 1 AND 2 OR date_dim.d_dom BETWEEN 25 AND 28)
      AND (household_demographics.hd_buy_potential='>10000' OR household_demographics.hd_buy_potential='Unknown')
      AND household_demographics.hd_vehicle_count>0
      AND (CASE WHEN household_demographics.hd_vehicle_count>0
                THEN household_demographics.hd_dep_count/household_demographics.hd_vehicle_count
                ELSE NULL END)>1
      AND date_dim.d_year IN (2000,2001,2002)
      AND store.s_county IN ('Williamson County','Franklin Parish','Bronx County','Orange County')
    GROUP BY ss_ticket_number,ss_customer_sk
) dj,customer
WHERE ss_customer_sk=c_customer_sk AND cnt BETWEEN 1 AND 5
ORDER BY cnt DESC,c_last_name LIMIT 100;
"""},
    {"id": 74, "name": "Customer YoY Store vs Web Growth", "sql": """
WITH year_total AS (
    SELECT c_customer_id customer_id,c_first_name customer_first_name,
           c_last_name customer_last_name,d_year dyear,MAX(ss_net_paid) year_total,'s' sale_type
    FROM customer,store_sales,date_dim
    WHERE c_customer_sk=ss_customer_sk AND ss_sold_date_sk=d_date_sk
    GROUP BY c_customer_id,c_first_name,c_last_name,d_year
    UNION ALL
    SELECT c_customer_id,c_first_name,c_last_name,d_year,MAX(ws_net_paid),'w'
    FROM customer,web_sales,date_dim
    WHERE c_customer_sk=ws_bill_customer_sk AND ws_sold_date_sk=d_date_sk
    GROUP BY c_customer_id,c_first_name,c_last_name,d_year
)
SELECT t_s_secyear.customer_id,t_s_secyear.customer_first_name,t_s_secyear.customer_last_name
FROM year_total t_s_firstyear,year_total t_s_secyear,year_total t_w_firstyear,year_total t_w_secyear
WHERE t_s_firstyear.customer_id=t_s_secyear.customer_id
  AND t_s_firstyear.customer_id=t_w_firstyear.customer_id
  AND t_s_firstyear.customer_id=t_w_secyear.customer_id
  AND t_s_firstyear.sale_type='s' AND t_w_firstyear.sale_type='w'
  AND t_s_secyear.sale_type='s'   AND t_w_secyear.sale_type='w'
  AND t_s_firstyear.dyear=2001 AND t_s_secyear.dyear=2002
  AND t_w_firstyear.dyear=2001 AND t_w_secyear.dyear=2002
  AND t_s_firstyear.year_total>0 AND t_w_firstyear.year_total>0
  AND CASE WHEN t_w_firstyear.year_total>0 THEN t_w_secyear.year_total/t_w_firstyear.year_total ELSE 0 END
    > CASE WHEN t_s_firstyear.year_total>0 THEN t_s_secyear.year_total/t_s_firstyear.year_total ELSE 0 END
ORDER BY t_s_secyear.customer_id,t_s_secyear.customer_first_name,t_s_secyear.customer_last_name LIMIT 100;
"""},
    {"id": 75, "name": "Multi-Channel Category Sales YoY", "sql": """
WITH all_sales AS (
    SELECT d_year,i_brand_id,i_class_id,i_category_id,i_manufact_id,
           SUM(sales_cnt) sales_cnt,SUM(sales_amt) sales_amt
    FROM (
        SELECT d_year,i_brand_id,i_class_id,i_category_id,i_manufact_id,
               cs_quantity-COALESCE(cr_return_quantity,0) sales_cnt,
               cs_ext_sales_price-COALESCE(cr_return_amount,0) sales_amt
        FROM catalog_sales JOIN item ON i_item_sk=cs_item_sk JOIN date_dim ON d_date_sk=cs_sold_date_sk
        LEFT JOIN catalog_returns ON cs_order_number=cr_order_number AND cs_item_sk=cr_item_sk
        WHERE i_category='Books'
        UNION
        SELECT d_year,i_brand_id,i_class_id,i_category_id,i_manufact_id,
               ss_quantity-COALESCE(sr_return_quantity,0),ss_ext_sales_price-COALESCE(sr_return_amt,0)
        FROM store_sales JOIN item ON i_item_sk=ss_item_sk JOIN date_dim ON d_date_sk=ss_sold_date_sk
        LEFT JOIN store_returns ON ss_ticket_number=sr_ticket_number AND ss_item_sk=sr_item_sk
        WHERE i_category='Books'
        UNION
        SELECT d_year,i_brand_id,i_class_id,i_category_id,i_manufact_id,
               ws_quantity-COALESCE(wr_return_quantity,0),ws_ext_sales_price-COALESCE(wr_return_amt,0)
        FROM web_sales JOIN item ON i_item_sk=ws_item_sk JOIN date_dim ON d_date_sk=ws_sold_date_sk
        LEFT JOIN web_returns ON ws_order_number=wr_order_number AND ws_item_sk=wr_item_sk
        WHERE i_category='Books'
    ) sales_detail
    GROUP BY d_year,i_brand_id,i_class_id,i_category_id,i_manufact_id
)
SELECT prev_yr.d_year prev_year, curr_yr.d_year curr_year,
       curr_yr.i_brand_id,curr_yr.i_class_id,curr_yr.i_category_id,curr_yr.i_manufact_id,
       prev_yr.sales_cnt prev_yr_cnt,curr_yr.sales_cnt curr_yr_cnt,
       curr_yr.sales_cnt-prev_yr.sales_cnt sales_cnt_diff,
       curr_yr.sales_amt-prev_yr.sales_amt sales_amt_diff
FROM all_sales curr_yr,all_sales prev_yr
WHERE curr_yr.i_brand_id=prev_yr.i_brand_id AND curr_yr.i_class_id=prev_yr.i_class_id
  AND curr_yr.i_category_id=prev_yr.i_category_id AND curr_yr.i_manufact_id=prev_yr.i_manufact_id
  AND curr_yr.d_year=2002 AND prev_yr.d_year=2001
  AND CAST(curr_yr.sales_cnt AS DECIMAL(17,2))/CAST(prev_yr.sales_cnt AS DECIMAL(17,2))<0.9
ORDER BY sales_cnt_diff LIMIT 100;
"""},
]
    +
[
    {"id": 76, "name": "Channel NULL Sales", "sql": """
SELECT channel,col_name,d_year,d_qoy,i_category,COUNT(*) sales_cnt,SUM(ext_sales_price) sales_amt
FROM (
    SELECT 'store' channel,'ss_promo_sk' col_name,d_year,d_qoy,i_category,ss_ext_sales_price ext_sales_price
    FROM store_sales,item,date_dim WHERE ss_promo_sk IS NULL AND ss_sold_date_sk=d_date_sk AND ss_item_sk=i_item_sk
    UNION ALL
    SELECT 'web','ws_promo_sk',d_year,d_qoy,i_category,ws_ext_sales_price
    FROM web_sales,item,date_dim WHERE ws_promo_sk IS NULL AND ws_sold_date_sk=d_date_sk AND ws_item_sk=i_item_sk
    UNION ALL
    SELECT 'catalog','cs_promo_sk',d_year,d_qoy,i_category,cs_ext_sales_price
    FROM catalog_sales,item,date_dim WHERE cs_promo_sk IS NULL AND cs_sold_date_sk=d_date_sk AND cs_item_sk=i_item_sk
) foo
GROUP BY channel,col_name,d_year,d_qoy,i_category
ORDER BY channel,col_name,d_year,d_qoy,i_category LIMIT 100;
"""},
    {"id": 77, "name": "Multi-Channel Sales and Returns Aggregation", "sql": """
WITH ss AS (
    SELECT s_store_sk,SUM(ss_ext_sales_price) sales,SUM(ss_net_profit) profit
    FROM store_sales,date_dim,store
    WHERE ss_sold_date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-08-23' AS DATE) AND CAST('2000-08-23' AS DATE)+INTERVAL'30 days'
      AND ss_store_sk=s_store_sk GROUP BY s_store_sk
),
sr AS (
    SELECT s_store_sk,SUM(sr_return_amt) returns_,SUM(sr_net_loss) profit_loss
    FROM store_returns,date_dim,store
    WHERE sr_returned_date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-08-23' AS DATE) AND CAST('2000-08-23' AS DATE)+INTERVAL'30 days'
      AND sr_store_sk=s_store_sk GROUP BY s_store_sk
),
cs AS (
    SELECT cs_call_center_sk,SUM(cs_ext_sales_price) sales,SUM(cs_net_profit) profit
    FROM catalog_sales,date_dim WHERE cs_sold_date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-08-23' AS DATE) AND CAST('2000-08-23' AS DATE)+INTERVAL'30 days'
    GROUP BY cs_call_center_sk
),
cr AS (
    SELECT SUM(cr_return_amount) returns_,SUM(cr_net_loss) profit_loss
    FROM catalog_returns,date_dim WHERE cr_returned_date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-08-23' AS DATE) AND CAST('2000-08-23' AS DATE)+INTERVAL'30 days'
),
ws AS (
    SELECT wp_web_page_sk,SUM(ws_ext_sales_price) sales,SUM(ws_net_profit) profit
    FROM web_sales,date_dim,web_page WHERE ws_sold_date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-08-23' AS DATE) AND CAST('2000-08-23' AS DATE)+INTERVAL'30 days'
      AND ws_web_page_sk=wp_web_page_sk GROUP BY wp_web_page_sk
),
wr AS (
    SELECT wp_web_page_sk,SUM(wr_return_amt) returns_,SUM(wr_net_loss) profit_loss
    FROM web_returns,date_dim,web_page WHERE wr_returned_date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-08-23' AS DATE) AND CAST('2000-08-23' AS DATE)+INTERVAL'30 days'
      AND wr_web_page_sk=wp_web_page_sk GROUP BY wp_web_page_sk
)
SELECT channel,id,SUM(sales) sales,SUM(returns_) returns_,SUM(profit) profit
FROM (
    SELECT 'store channel' channel,ss.s_store_sk id,sales,COALESCE(returns_,0) returns_,(profit-COALESCE(profit_loss,0)) profit FROM ss LEFT JOIN sr ON ss.s_store_sk=sr.s_store_sk
    UNION ALL
    SELECT 'catalog channel',cs_call_center_sk,sales,returns_,(profit-profit_loss) FROM cs,cr
    UNION ALL
    SELECT 'web channel',ws.wp_web_page_sk,sales,COALESCE(returns_,0),(profit-COALESCE(profit_loss,0)) FROM ws LEFT JOIN wr ON ws.wp_web_page_sk=wr.wp_web_page_sk
) x
GROUP BY ROLLUP(channel,id) ORDER BY channel,id LIMIT 100;
"""},
    {"id": 78, "name": "Cross-Channel Non-Returned Sales", "sql": """
WITH ws AS (
    SELECT d_year ws_sold_year,ws_item_sk,ws_bill_customer_sk ws_customer_sk,
           SUM(ws_quantity) ws_qty,SUM(ws_wholesale_cost) ws_wc,SUM(ws_sales_price) ws_sp
    FROM web_sales LEFT JOIN web_returns ON wr_order_number=ws_order_number AND wr_item_sk=ws_item_sk
    JOIN date_dim ON ws_sold_date_sk=d_date_sk
    WHERE wr_order_number IS NULL
    GROUP BY d_year,ws_item_sk,ws_bill_customer_sk
),
cs AS (
    SELECT d_year cs_sold_year,cs_item_sk,cs_bill_customer_sk cs_customer_sk,
           SUM(cs_quantity) cs_qty,SUM(cs_wholesale_cost) cs_wc,SUM(cs_sales_price) cs_sp
    FROM catalog_sales LEFT JOIN catalog_returns ON cr_order_number=cs_order_number AND cr_item_sk=cs_item_sk
    JOIN date_dim ON cs_sold_date_sk=d_date_sk
    WHERE cr_order_number IS NULL
    GROUP BY d_year,cs_item_sk,cs_bill_customer_sk
),
ss AS (
    SELECT d_year ss_sold_year,ss_item_sk,ss_customer_sk,
           SUM(ss_quantity) ss_qty,SUM(ss_wholesale_cost) ss_wc,SUM(ss_sales_price) ss_sp
    FROM store_sales LEFT JOIN store_returns ON sr_ticket_number=ss_ticket_number AND sr_item_sk=ss_item_sk
    JOIN date_dim ON ss_sold_date_sk=d_date_sk
    WHERE sr_ticket_number IS NULL
    GROUP BY d_year,ss_item_sk,ss_customer_sk
)
SELECT ss_sold_year,ss_item_sk,ss_customer_sk,
       ROUND(ss_qty/(COALESCE(ws_qty,0)+COALESCE(cs_qty,0)+ss_qty),2) store_pct,
       ROUND(ss_qty,2) store_qty, ROUND(ss_wc,2) store_wholesale_cost, ROUND(ss_sp,2) store_sales_price
FROM ss LEFT JOIN ws ON ws_sold_year=ss_sold_year AND ws_item_sk=ss_item_sk AND ws_customer_sk=ss_customer_sk
        LEFT JOIN cs ON cs_sold_year=ss_sold_year AND cs_item_sk=ss_item_sk AND cs_customer_sk=ss_customer_sk
WHERE (COALESCE(ws_qty,0)+COALESCE(cs_qty,0)+ss_qty)>0 AND ss_sold_year=2001
ORDER BY ss_sold_year,ss_item_sk,ss_customer_sk,store_pct,store_qty,store_wholesale_cost,store_sales_price LIMIT 100;
"""},
    {"id": 79, "name": "Store Sales by Manager Household", "sql": """
SELECT c_last_name,c_first_name,SUBSTR(s_city,1,30) s_city,ss_ticket_number,amt,profit
FROM (
    SELECT ss_ticket_number,ss_customer_sk,store.s_city,
           SUM(ss_coupon_amt) amt,SUM(ss_net_profit) profit
    FROM store_sales,date_dim,store,household_demographics
    WHERE store_sales.ss_sold_date_sk=date_dim.d_date_sk AND store_sales.ss_store_sk=store.s_store_sk
      AND store_sales.ss_hdemo_sk=household_demographics.hd_demo_sk
      AND (household_demographics.hd_dep_count=6 OR household_demographics.hd_vehicle_count>2)
      AND date_dim.d_dow=1 AND date_dim.d_year IN (1999,2000,2001)
      AND store.s_number_employees BETWEEN 200 AND 295
    GROUP BY ss_ticket_number,ss_customer_sk,ss_addr_sk,store.s_city
) ms,customer
WHERE ss_customer_sk=c_customer_sk
ORDER BY c_last_name,c_first_name,SUBSTR(s_city,1,30),profit LIMIT 100;
"""},
    {"id": 80, "name": "Multi-Channel Store Profit Analysis", "sql": """
WITH ssr AS (
    SELECT s_store_id store_id,
           SUM(ss_ext_sales_price) sales,SUM(COALESCE(sr_return_amt,0)) returns_,
           SUM(ss_net_profit-COALESCE(sr_net_loss,0)) profit
    FROM store_sales LEFT JOIN store_returns ON ss_item_sk=sr_item_sk AND ss_ticket_number=sr_ticket_number,
         date_dim,store,item,promotion
    WHERE ss_sold_date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-08-23' AS DATE) AND CAST('2000-08-23' AS DATE)+INTERVAL'30 days'
      AND ss_store_sk=s_store_sk AND ss_item_sk=i_item_sk AND i_current_price>50
      AND ss_promo_sk=p_promo_sk AND p_channel_tv='N'
    GROUP BY s_store_id
),
csr AS (
    SELECT cp_catalog_page_id catalog_page_id,
           SUM(cs_ext_sales_price) sales,SUM(COALESCE(cr_return_amount,0)) returns_,
           SUM(cs_net_profit-COALESCE(cr_net_loss,0)) profit
    FROM catalog_sales LEFT JOIN catalog_returns ON cs_item_sk=cr_item_sk AND cs_order_number=cr_order_number,
         date_dim,catalog_page,item,promotion
    WHERE cs_sold_date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-08-23' AS DATE) AND CAST('2000-08-23' AS DATE)+INTERVAL'30 days'
      AND cs_catalog_page_sk=cp_catalog_page_sk AND cs_item_sk=i_item_sk AND i_current_price>50
      AND cs_promo_sk=p_promo_sk AND p_channel_tv='N'
    GROUP BY cp_catalog_page_id
),
wsr AS (
    SELECT web_site_id,
           SUM(ws_ext_sales_price) sales,SUM(COALESCE(wr_return_amt,0)) returns_,
           SUM(ws_net_profit-COALESCE(wr_net_loss,0)) profit
    FROM web_sales LEFT JOIN web_returns ON ws_item_sk=wr_item_sk AND ws_order_number=wr_order_number,
         date_dim,web_site,item,promotion
    WHERE ws_sold_date_sk=d_date_sk
      AND d_date BETWEEN CAST('2000-08-23' AS DATE) AND CAST('2000-08-23' AS DATE)+INTERVAL'30 days'
      AND ws_web_site_sk=web_site_sk AND ws_item_sk=i_item_sk AND i_current_price>50
      AND ws_promo_sk=p_promo_sk AND p_channel_tv='N'
    GROUP BY web_site_id
)
SELECT channel,id,SUM(sales) sales,SUM(returns_) returns_,SUM(profit) profit
FROM (
    SELECT 'store' channel,store_id id,sales,returns_,profit FROM ssr
    UNION ALL
    SELECT 'catalog',catalog_page_id,sales,returns_,profit FROM csr
    UNION ALL
    SELECT 'web',web_site_id,sales,returns_,profit FROM wsr
) x
GROUP BY ROLLUP(channel,id) ORDER BY channel,id LIMIT 100;
"""},
    {"id": 81, "name": "Catalog Returns by Customer State", "sql": """
WITH customer_total_return AS (
    SELECT cr_returning_customer_sk ctr_customer_sk, ca_state ctr_state,
           SUM(cr_return_amt_inc_tax) ctr_total_return
    FROM catalog_returns,date_dim,customer_address
    WHERE cr_returned_date_sk=d_date_sk AND d_year=2000 AND cr_returning_addr_sk=ca_address_sk
    GROUP BY cr_returning_customer_sk,ca_state
)
SELECT c_customer_id,c_salutation,c_first_name,c_last_name,
       ca_street_number,ca_street_name,ca_street_type,ca_suite_number,
       ca_city,ca_county,ca_state,ca_zip,ca_country,ca_gmt_offset,ca_location_type,ctr_total_return
FROM customer_total_return ctr1,customer_address,customer
WHERE ctr1.ctr_total_return>(SELECT AVG(ctr_total_return)*1.2 FROM customer_total_return ctr2 WHERE ctr1.ctr_state=ctr2.ctr_state)
  AND ca_address_sk=c_current_addr_sk AND ca_state='GA' AND ctr1.ctr_customer_sk=c_customer_sk
ORDER BY c_customer_id,c_salutation,c_first_name,c_last_name,ca_street_number,ca_street_name,
         ca_street_type,ca_suite_number,ca_city,ca_county,ca_state,ca_zip,ca_country,
         ca_gmt_offset,ca_location_type,ctr_total_return LIMIT 100;
"""},
    {"id": 82, "name": "Item Inventory Demand", "sql": """
SELECT i_item_id,i_item_desc,i_current_price
FROM item,inventory,date_dim,store_sales
WHERE i_current_price BETWEEN 62 AND 92 AND inv_item_sk=i_item_sk
  AND d_date_sk=inv_date_sk
  AND d_date BETWEEN CAST('2000-05-25' AS DATE) AND CAST('2000-05-25' AS DATE)+INTERVAL'60 days'
  AND i_manufact_id IN (129,270,821,423)
  AND inv_quantity_on_hand BETWEEN 100 AND 500 AND ss_item_sk=i_item_sk
GROUP BY i_item_id,i_item_desc,i_current_price ORDER BY i_item_id LIMIT 100;
"""},
    {"id": 83, "name": "Store Returns by Reason", "sql": """
WITH sr_items AS (
    SELECT i_item_id item_id,SUM(sr_return_quantity) sr_item_qty
    FROM store_returns,item,date_dim WHERE sr_item_sk=i_item_sk AND d_date_sk=sr_returned_date_sk
      AND d_date IN (SELECT d_date FROM date_dim WHERE d_week_seq IN (
          SELECT d_week_seq FROM date_dim WHERE d_date IN (CAST('2000-06-30' AS DATE),CAST('2000-09-27' AS DATE),CAST('2000-11-17' AS DATE))))
    GROUP BY i_item_id
),
cr_items AS (
    SELECT i_item_id item_id,SUM(cr_return_quantity) cr_item_qty
    FROM catalog_returns,item,date_dim WHERE cr_item_sk=i_item_sk AND d_date_sk=cr_returned_date_sk
      AND d_date IN (SELECT d_date FROM date_dim WHERE d_week_seq IN (
          SELECT d_week_seq FROM date_dim WHERE d_date IN (CAST('2000-06-30' AS DATE),CAST('2000-09-27' AS DATE),CAST('2000-11-17' AS DATE))))
    GROUP BY i_item_id
),
wr_items AS (
    SELECT i_item_id item_id,SUM(wr_return_quantity) wr_item_qty
    FROM web_returns,item,date_dim WHERE wr_item_sk=i_item_sk AND d_date_sk=wr_returned_date_sk
      AND d_date IN (SELECT d_date FROM date_dim WHERE d_week_seq IN (
          SELECT d_week_seq FROM date_dim WHERE d_date IN (CAST('2000-06-30' AS DATE),CAST('2000-09-27' AS DATE),CAST('2000-11-17' AS DATE))))
    GROUP BY i_item_id
)
SELECT sr_items.item_id,sr_item_qty,sr_item_qty/(sr_item_qty+cr_item_qty+wr_item_qty)*100 sr_dev,
       cr_item_qty,cr_item_qty/(sr_item_qty+cr_item_qty+wr_item_qty)*100 cr_dev,
       wr_item_qty,wr_item_qty/(sr_item_qty+cr_item_qty+wr_item_qty)*100 wr_dev,
       (sr_item_qty+cr_item_qty+wr_item_qty) total_qty
FROM sr_items,cr_items,wr_items
WHERE sr_items.item_id=cr_items.item_id AND sr_items.item_id=wr_items.item_id
ORDER BY sr_items.item_id,sr_item_qty LIMIT 100;
"""},
    {"id": 84, "name": "Customer Activity by Income Band", "sql": """
SELECT c_customer_id customer_id,
       COALESCE(c_last_name,'')||', '||COALESCE(c_first_name,'') customername
FROM customer,customer_address,customer_demographics,household_demographics,income_band,store_returns
WHERE ca_city='Edgewood' AND c_current_addr_sk=ca_address_sk
  AND ib_lower_bound>=38128 AND ib_upper_bound<=88128
  AND ib_income_band_sk=hd_income_band_sk AND cd_demo_sk=c_current_cdemo_sk
  AND hd_demo_sk=c_current_hdemo_sk AND sr_cdemo_sk=cd_demo_sk
ORDER BY c_customer_id LIMIT 100;
"""},
    {"id": 85, "name": "Web Returns Demographic Analysis", "sql": """
SELECT SUBSTR(r_reason_desc,1,20) reason, AVG(ws_quantity) avg_qty,
       AVG(wr_refunded_cash) avg_refunded_cash, AVG(wr_fee) avg_fee
FROM web_sales,web_returns,web_page,customer_demographics cd1,customer_demographics cd2,
     customer_address,date_dim,reason
WHERE ws_web_page_sk=wp_web_page_sk AND ws_item_sk=wr_item_sk AND ws_order_number=wr_order_number
  AND ws_sold_date_sk=d_date_sk AND d_year=2000
  AND cd1.cd_demo_sk=wr_refunded_cdemo_sk AND cd2.cd_demo_sk=wr_returning_cdemo_sk
  AND ca_address_sk=wr_refunded_addr_sk AND r_reason_sk=wr_reason_sk
  AND ((cd1.cd_marital_status='M' AND cd1.cd_marital_status=cd2.cd_marital_status AND cd1.cd_education_status='Advanced Degree' AND cd1.cd_education_status=cd2.cd_education_status AND ws_sales_price BETWEEN 100.00 AND 150.00)
    OR (cd1.cd_marital_status='S' AND cd1.cd_marital_status=cd2.cd_marital_status AND cd1.cd_education_status='College' AND cd1.cd_education_status=cd2.cd_education_status AND ws_sales_price BETWEEN 50.00 AND 100.00)
    OR (cd1.cd_marital_status='W' AND cd1.cd_marital_status=cd2.cd_marital_status AND cd1.cd_education_status='2 yr Degree' AND cd1.cd_education_status=cd2.cd_education_status AND ws_sales_price BETWEEN 150.00 AND 200.00))
  AND ((ca_country='United States' AND ca_state IN ('IN','OH','NJ') AND ws_net_profit BETWEEN 100 AND 200)
    OR (ca_country='United States' AND ca_state IN ('WI','CT','KY') AND ws_net_profit BETWEEN 150 AND 300)
    OR (ca_country='United States' AND ca_state IN ('LA','IA','AR') AND ws_net_profit BETWEEN 50 AND 250))
GROUP BY r_reason_desc ORDER BY SUBSTR(r_reason_desc,1,20),avg_qty,avg_refunded_cash,avg_fee LIMIT 100;
"""},
    {"id": 86, "name": "Web Sales Rollup by Category", "sql": """
SELECT * FROM (
    SELECT SUM(ws_net_paid) total_sum, i_category, i_class,
           GROUPING(i_category)+GROUPING(i_class) lochierarchy,
           RANK() OVER (
               PARTITION BY GROUPING(i_category)+GROUPING(i_class),
                            CASE WHEN GROUPING(i_class)=0 THEN i_category END
               ORDER BY SUM(ws_net_paid) DESC
           ) rank_within_parent
    FROM web_sales,date_dim d1,item
    WHERE d1.d_month_seq BETWEEN 1200 AND 1211 AND d1.d_date_sk=ws_sold_date_sk AND i_item_sk=ws_item_sk
    GROUP BY ROLLUP(i_category,i_class)
) q86
ORDER BY lochierarchy DESC, CASE WHEN lochierarchy=0 THEN i_category END, rank_within_parent LIMIT 100;
"""},
    {"id": 87, "name": "Customers Only in Store Channel", "sql": """
SELECT COUNT(*) cnt FROM (
    SELECT DISTINCT c_last_name,c_first_name,d_date
    FROM store_sales,date_dim,customer
    WHERE store_sales.ss_sold_date_sk=date_dim.d_date_sk AND store_sales.ss_customer_sk=customer.c_customer_sk
      AND d_month_seq BETWEEN 1200 AND 1211
    EXCEPT
    SELECT DISTINCT c_last_name,c_first_name,d_date
    FROM catalog_sales,date_dim,customer
    WHERE catalog_sales.cs_sold_date_sk=date_dim.d_date_sk AND catalog_sales.cs_bill_customer_sk=customer.c_customer_sk
      AND d_month_seq BETWEEN 1200 AND 1211
    EXCEPT
    SELECT DISTINCT c_last_name,c_first_name,d_date
    FROM web_sales,date_dim,customer
    WHERE web_sales.ws_sold_date_sk=date_dim.d_date_sk AND web_sales.ws_bill_customer_sk=customer.c_customer_sk
      AND d_month_seq BETWEEN 1200 AND 1211
) cool_cust LIMIT 100;
"""},
    {"id": 88, "name": "Store Sales Time Buckets", "sql": """
SELECT * FROM
  (SELECT COUNT(*) h8_30_to_9 FROM store_sales,household_demographics,time_dim,store
   WHERE ss_sold_time_sk=time_dim.t_time_sk AND ss_hdemo_sk=household_demographics.hd_demo_sk
     AND ss_store_sk=s_store_sk AND time_dim.t_hour=8 AND time_dim.t_minute>=30
     AND ((hd_dep_count=3 AND hd_vehicle_count<=5) OR (hd_dep_count=0 AND hd_vehicle_count<=2) OR (hd_dep_count=1 AND hd_vehicle_count<=3))
     AND store.s_store_name='ese') s1,
  (SELECT COUNT(*) h9_to_9_30 FROM store_sales,household_demographics,time_dim,store
   WHERE ss_sold_time_sk=time_dim.t_time_sk AND ss_hdemo_sk=household_demographics.hd_demo_sk
     AND ss_store_sk=s_store_sk AND time_dim.t_hour=9 AND time_dim.t_minute<30
     AND ((hd_dep_count=3 AND hd_vehicle_count<=5) OR (hd_dep_count=0 AND hd_vehicle_count<=2) OR (hd_dep_count=1 AND hd_vehicle_count<=3))
     AND store.s_store_name='ese') s2,
  (SELECT COUNT(*) h9_30_to_10 FROM store_sales,household_demographics,time_dim,store
   WHERE ss_sold_time_sk=time_dim.t_time_sk AND ss_hdemo_sk=household_demographics.hd_demo_sk
     AND ss_store_sk=s_store_sk AND time_dim.t_hour=9 AND time_dim.t_minute>=30
     AND ((hd_dep_count=3 AND hd_vehicle_count<=5) OR (hd_dep_count=0 AND hd_vehicle_count<=2) OR (hd_dep_count=1 AND hd_vehicle_count<=3))
     AND store.s_store_name='ese') s3,
  (SELECT COUNT(*) h10_to_10_30 FROM store_sales,household_demographics,time_dim,store
   WHERE ss_sold_time_sk=time_dim.t_time_sk AND ss_hdemo_sk=household_demographics.hd_demo_sk
     AND ss_store_sk=s_store_sk AND time_dim.t_hour=10 AND time_dim.t_minute<30
     AND ((hd_dep_count=3 AND hd_vehicle_count<=5) OR (hd_dep_count=0 AND hd_vehicle_count<=2) OR (hd_dep_count=1 AND hd_vehicle_count<=3))
     AND store.s_store_name='ese') s4,
  (SELECT COUNT(*) h10_30_to_11 FROM store_sales,household_demographics,time_dim,store
   WHERE ss_sold_time_sk=time_dim.t_time_sk AND ss_hdemo_sk=household_demographics.hd_demo_sk
     AND ss_store_sk=s_store_sk AND time_dim.t_hour=10 AND time_dim.t_minute>=30
     AND ((hd_dep_count=3 AND hd_vehicle_count<=5) OR (hd_dep_count=0 AND hd_vehicle_count<=2) OR (hd_dep_count=1 AND hd_vehicle_count<=3))
     AND store.s_store_name='ese') s5,
  (SELECT COUNT(*) h11_to_11_30 FROM store_sales,household_demographics,time_dim,store
   WHERE ss_sold_time_sk=time_dim.t_time_sk AND ss_hdemo_sk=household_demographics.hd_demo_sk
     AND ss_store_sk=s_store_sk AND time_dim.t_hour=11 AND time_dim.t_minute<30
     AND ((hd_dep_count=3 AND hd_vehicle_count<=5) OR (hd_dep_count=0 AND hd_vehicle_count<=2) OR (hd_dep_count=1 AND hd_vehicle_count<=3))
     AND store.s_store_name='ese') s6,
  (SELECT COUNT(*) h11_30_to_12 FROM store_sales,household_demographics,time_dim,store
   WHERE ss_sold_time_sk=time_dim.t_time_sk AND ss_hdemo_sk=household_demographics.hd_demo_sk
     AND ss_store_sk=s_store_sk AND time_dim.t_hour=11 AND time_dim.t_minute>=30
     AND ((hd_dep_count=3 AND hd_vehicle_count<=5) OR (hd_dep_count=0 AND hd_vehicle_count<=2) OR (hd_dep_count=1 AND hd_vehicle_count<=3))
     AND store.s_store_name='ese') s7,
  (SELECT COUNT(*) h12_to_12_30 FROM store_sales,household_demographics,time_dim,store
   WHERE ss_sold_time_sk=time_dim.t_time_sk AND ss_hdemo_sk=household_demographics.hd_demo_sk
     AND ss_store_sk=s_store_sk AND time_dim.t_hour=12 AND time_dim.t_minute<30
     AND ((hd_dep_count=3 AND hd_vehicle_count<=5) OR (hd_dep_count=0 AND hd_vehicle_count<=2) OR (hd_dep_count=1 AND hd_vehicle_count<=3))
     AND store.s_store_name='ese') s8;
"""},
    {"id": 89, "name": "Store Sales by Category Class Brand", "sql": """
SELECT * FROM (
    SELECT i_category,i_class,i_brand,s_store_name,s_company_name,d_moy,
           SUM(ss_sales_price) sum_sales,
           AVG(SUM(ss_sales_price)) OVER (PARTITION BY i_category,i_brand,s_store_name,s_company_name) avg_monthly_sales
    FROM item,store_sales,date_dim,store
    WHERE ss_item_sk=i_item_sk AND ss_sold_date_sk=d_date_sk AND ss_store_sk=s_store_sk AND d_year=1999
      AND ((i_category IN ('Holiday','Electronics','Books') AND i_class IN ('personal','portable','reference','self-help') AND i_brand_id IN (1001001,1001002,1001003,1001004))
        OR (i_category IN ('Women','Men','Shoes') AND i_class IN ('accessories','classical','fragrances','pants') AND i_brand_id IN (2001001,2001002,2001003,2001004))
        OR (i_category IN ('Music','Children','Jewelry') AND i_class IN ('accessories','classical','fragrances','pants') AND i_brand_id IN (3001001,3001002,3001003,3001004)))
    GROUP BY i_category,i_class,i_brand,s_store_name,s_company_name,d_moy
) tmp1
WHERE CASE WHEN avg_monthly_sales>0 THEN ABS(sum_sales-avg_monthly_sales)/avg_monthly_sales ELSE NULL END>0.1
ORDER BY sum_sales-avg_monthly_sales,avg_monthly_sales LIMIT 100;
"""},
    {"id": 90, "name": "Web Store AM PM Ratio", "sql": """
SELECT CAST(amc AS DECIMAL(15,4))/CAST(pmc AS DECIMAL(15,4)) am_pm_ratio
FROM (SELECT COUNT(*) amc FROM web_sales,household_demographics,time_dim,web_page
      WHERE ws_sold_time_sk=time_dim.t_time_sk AND ws_ship_hdemo_sk=household_demographics.hd_demo_sk
        AND ws_web_page_sk=web_page.wp_web_page_sk AND time_dim.t_hour BETWEEN 8 AND 9
        AND household_demographics.hd_dep_count=0 AND web_page.wp_char_count BETWEEN 5000 AND 5200) at_,
     (SELECT COUNT(*) pmc FROM web_sales,household_demographics,time_dim,web_page
      WHERE ws_sold_time_sk=time_dim.t_time_sk AND ws_ship_hdemo_sk=household_demographics.hd_demo_sk
        AND ws_web_page_sk=web_page.wp_web_page_sk AND time_dim.t_hour BETWEEN 19 AND 20
        AND household_demographics.hd_dep_count=0 AND web_page.wp_char_count BETWEEN 5000 AND 5200) pt
ORDER BY am_pm_ratio LIMIT 100;
"""},
    {"id": 91, "name": "Call Center Returns Analysis", "sql": """
SELECT cc_call_center_id Call_Center, cc_name Call_Center_Name, cc_manager Manager,
       SUM(cr_net_loss) Returns_Loss
FROM catalog_returns,date_dim,customer,customer_address,customer_demographics,household_demographics,call_center
WHERE cr_returned_date_sk=d_date_sk AND d_year=1998 AND d_moy=11
  AND cr_returning_customer_sk=c_customer_sk AND ca_address_sk=c_current_addr_sk
  AND ca_gmt_offset=-7 AND cr_call_center_sk=cc_call_center_sk
  AND cd_demo_sk=c_current_cdemo_sk AND hd_demo_sk=c_current_hdemo_sk
  AND (cd_marital_status='M' OR cd_marital_status='U')
  AND (cd_education_status='Advanced Degree' OR cd_education_status='College' OR cd_education_status='4 yr Degree')
  AND (hd_buy_potential LIKE 'Unknown%' OR hd_buy_potential LIKE '>10000%')
  AND hd_vehicle_count>0
GROUP BY cc_call_center_id,cc_name,cc_manager,cd_marital_status,cd_education_status
ORDER BY SUM(cr_net_loss) DESC LIMIT 100;
"""},
    {"id": 92, "name": "Web Sales Excess Discount", "sql": """
SELECT SUM(ws_ext_discount_amt) excess_discount_amount
FROM web_sales,item,date_dim
WHERE i_manufact_id=350 AND i_item_sk=ws_item_sk
  AND d_date BETWEEN CAST('2000-01-27' AS DATE) AND CAST('2000-01-27' AS DATE)+INTERVAL'90 days'
  AND d_date_sk=ws_sold_date_sk
  AND ws_ext_discount_amt>(SELECT 1.3*AVG(ws_ext_discount_amt) FROM web_sales,date_dim
      WHERE ws_item_sk=i_item_sk
        AND d_date BETWEEN CAST('2000-01-27' AS DATE) AND CAST('2000-01-27' AS DATE)+INTERVAL'90 days'
        AND d_date_sk=ws_sold_date_sk)
ORDER BY SUM(ws_ext_discount_amt) DESC LIMIT 100;
"""},
    {"id": 93, "name": "Store Sales Returns Loss", "sql": """
SELECT ss_customer_sk, SUM(act_sales) sumsales
FROM (
    SELECT ss_item_sk,ss_ticket_number,ss_customer_sk,
           CASE WHEN sr_return_quantity IS NOT NULL
                THEN (ss_quantity-sr_return_quantity)*ss_sales_price
                ELSE (ss_quantity*ss_sales_price) END act_sales
    FROM store_sales
    LEFT OUTER JOIN store_returns ON (sr_item_sk=ss_item_sk AND sr_ticket_number=ss_ticket_number),
         reason
    WHERE sr_reason_sk=r_reason_sk AND r_reason_desc='Did not like the color'
) t
GROUP BY ss_customer_sk ORDER BY sumsales,ss_customer_sk LIMIT 100;
"""},
    {"id": 94, "name": "Web Sales Distinct Orders", "sql": """
SELECT COUNT(DISTINCT ws_order_number) order_count,
       SUM(ws_ext_ship_cost) total_shipping_cost, SUM(ws_net_profit) total_net_profit
FROM web_sales ws1,date_dim,customer_address,web_site
WHERE d_date BETWEEN CAST('2000-2-01' AS DATE) AND CAST('2000-2-01' AS DATE)+INTERVAL'60 days'
  AND ws1.ws_ship_date_sk=d_date_sk AND ws1.ws_ship_addr_sk=ca_address_sk
  AND ca_state='IL' AND ws1.ws_web_site_sk=web_site_sk AND web_site.web_company_name='pri'
  AND EXISTS (SELECT * FROM web_sales ws2 WHERE ws1.ws_order_number=ws2.ws_order_number AND ws1.ws_warehouse_sk<>ws2.ws_warehouse_sk)
  AND NOT EXISTS (SELECT * FROM web_returns wr1 WHERE ws1.ws_order_number=wr1.wr_order_number)
ORDER BY COUNT(DISTINCT ws_order_number) LIMIT 100;
"""},
    {"id": 95, "name": "Web Sales Returns Distinct Orders", "sql": """
WITH ws_wh AS (
    SELECT ws1.ws_order_number,ws1.ws_warehouse_sk wh1,ws2.ws_warehouse_sk wh2
    FROM web_sales ws1,web_sales ws2
    WHERE ws1.ws_order_number=ws2.ws_order_number AND ws1.ws_warehouse_sk<>ws2.ws_warehouse_sk
)
SELECT COUNT(DISTINCT ws_order_number) order_count,
       SUM(ws_ext_ship_cost) total_shipping_cost, SUM(ws_net_profit) total_net_profit
FROM web_sales,date_dim,customer_address,web_site
WHERE d_date BETWEEN CAST('2000-2-01' AS DATE) AND CAST('2000-2-01' AS DATE)+INTERVAL'60 days'
  AND ws_ship_date_sk=d_date_sk AND ws_ship_addr_sk=ca_address_sk AND ca_state='IL'
  AND ws_web_site_sk=web_site_sk AND web_site.web_company_name='pri'
  AND ws_order_number IN (SELECT ws_order_number FROM ws_wh)
  AND ws_order_number NOT IN (SELECT wr_order_number FROM web_returns,ws_wh WHERE wr_order_number=ws_wh.ws_order_number)
ORDER BY COUNT(DISTINCT ws_order_number) LIMIT 100;
"""},
    {"id": 96, "name": "Store Sales Time Analysis", "sql": """
SELECT COUNT(*) cnt
FROM store_sales,household_demographics,time_dim,store
WHERE ss_sold_time_sk=time_dim.t_time_sk AND ss_hdemo_sk=household_demographics.hd_demo_sk
  AND ss_store_sk=s_store_sk AND time_dim.t_hour=8 AND time_dim.t_minute>=30
  AND household_demographics.hd_dep_count=0 AND store.s_store_name='ese'
ORDER BY cnt LIMIT 100;
"""},
    {"id": 97, "name": "Store Catalog Item Overlap", "sql": """
WITH ssci AS (
    SELECT ss_customer_sk customer_sk,ss_item_sk item_sk
    FROM store_sales,date_dim WHERE ss_sold_date_sk=d_date_sk AND d_month_seq BETWEEN 1200 AND 1211
    GROUP BY ss_customer_sk,ss_item_sk
),
csci AS (
    SELECT cs_bill_customer_sk customer_sk,cs_item_sk item_sk
    FROM catalog_sales,date_dim WHERE cs_sold_date_sk=d_date_sk AND d_month_seq BETWEEN 1200 AND 1211
    GROUP BY cs_bill_customer_sk,cs_item_sk
)
SELECT SUM(CASE WHEN ssci.customer_sk IS NOT NULL AND csci.customer_sk IS NULL  THEN 1 ELSE 0 END) store_only,
       SUM(CASE WHEN ssci.customer_sk IS NULL     AND csci.customer_sk IS NOT NULL THEN 1 ELSE 0 END) catalog_only,
       SUM(CASE WHEN ssci.customer_sk IS NOT NULL AND csci.customer_sk IS NOT NULL THEN 1 ELSE 0 END) store_and_catalog
FROM ssci FULL OUTER JOIN csci ON ssci.customer_sk=csci.customer_sk AND ssci.item_sk=csci.item_sk LIMIT 100;
"""},
    {"id": 98, "name": "Store Sales by Item Category", "sql": """
SELECT i_item_id, i_item_desc, i_category, i_class, i_current_price,
       SUM(ss_ext_sales_price) itemrevenue,
       SUM(ss_ext_sales_price)*100/SUM(SUM(ss_ext_sales_price)) OVER (PARTITION BY i_class) revenueratio
FROM store_sales,item,date_dim
WHERE ss_item_sk=i_item_sk AND i_category IN ('Sports','Books','Home')
  AND ss_sold_date_sk=d_date_sk
  AND d_date BETWEEN CAST('1999-02-22' AS DATE) AND CAST('1999-02-22' AS DATE)+INTERVAL'30 days'
GROUP BY i_item_id,i_item_desc,i_category,i_class,i_current_price
ORDER BY i_category,i_class,i_item_id,i_item_desc,revenueratio LIMIT 100;
"""},
    {"id": 99, "name": "Catalog Sales by Warehouse Ship Mode", "sql": """
SELECT SUBSTR(w_warehouse_name,1,20) warehouse, sm_type, cc_name,
       SUM(CASE WHEN cs_ship_date_sk-cs_sold_date_sk<=30 THEN 1 ELSE 0 END) d30,
       SUM(CASE WHEN cs_ship_date_sk-cs_sold_date_sk>30 AND cs_ship_date_sk-cs_sold_date_sk<=60 THEN 1 ELSE 0 END) d31_60,
       SUM(CASE WHEN cs_ship_date_sk-cs_sold_date_sk>60 AND cs_ship_date_sk-cs_sold_date_sk<=90 THEN 1 ELSE 0 END) d61_90,
       SUM(CASE WHEN cs_ship_date_sk-cs_sold_date_sk>90 AND cs_ship_date_sk-cs_sold_date_sk<=120 THEN 1 ELSE 0 END) d91_120,
       SUM(CASE WHEN cs_ship_date_sk-cs_sold_date_sk>120 THEN 1 ELSE 0 END) gt120
FROM catalog_sales,warehouse,ship_mode,call_center,date_dim
WHERE d_month_seq BETWEEN 1200 AND 1211 AND cs_ship_date_sk=d_date_sk
  AND cs_warehouse_sk=w_warehouse_sk AND cs_ship_mode_sk=sm_ship_mode_sk AND cs_call_center_sk=cc_call_center_sk
GROUP BY SUBSTR(w_warehouse_name,1,20),sm_type,cc_name
ORDER BY SUBSTR(w_warehouse_name,1,20),sm_type,cc_name LIMIT 100;
"""},
]
)

# Índice rápido por ID
QUERIES_BY_ID: dict[int, dict] = {q["id"]: q for q in QUERIES}
