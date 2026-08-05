USE SCADA_FLOW;


CREATE TABLE TagHistory
(

    ID INT IDENTITY PRIMARY KEY,


    CompanyID INT,


    PLC_ID INT,


    TagName NVARCHAR(100),


    Value FLOAT,


    Timestamp DATETIME

);