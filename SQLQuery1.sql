CREATE DATABASE SCADA_Flow;
GO

USE SCADA_Flow;
GO


-- =====================================
-- Companies
-- =====================================

CREATE TABLE Companies
(
    CompanyID INT IDENTITY(1,1) PRIMARY KEY,

    CompanyName NVARCHAR(100) NOT NULL
);
GO



-- =====================================
-- Users
-- =====================================

CREATE TABLE Users
(
    UserID INT IDENTITY(1,1) PRIMARY KEY,

    Username NVARCHAR(50) UNIQUE NOT NULL,

    PasswordHash NVARCHAR(200) NOT NULL,

    CompanyID INT NULL,

    Role NVARCHAR(20) NOT NULL,

    Enabled BIT NOT NULL DEFAULT 1,

    FOREIGN KEY (CompanyID)
    REFERENCES Companies(CompanyID)
);
GO



-- =====================================
-- PLC Configuration
-- =====================================

CREATE TABLE PLCs
(
    PLC_ID INT IDENTITY(1,1) PRIMARY KEY,

    CompanyID INT NOT NULL,

    PLC_Name NVARCHAR(100),

    PLC_IP NVARCHAR(50),

    PLC_Port INT DEFAULT 502,

    Slave_ID INT DEFAULT 1,

    FOREIGN KEY (CompanyID)
    REFERENCES Companies(CompanyID)
);
GO



-- =====================================
-- Tags
-- =====================================

CREATE TABLE Tags
(
    TagID INT IDENTITY(1,1) PRIMARY KEY,

    CompanyID INT NOT NULL,

    TagName NVARCHAR(100) NOT NULL,

    RegisterAddress INT NOT NULL,

    DataType NVARCHAR(20) DEFAULT 'INT',

    Description NVARCHAR(200),

    FOREIGN KEY (CompanyID)
    REFERENCES Companies(CompanyID)
);
GO



-- =====================================
-- Historian
-- =====================================

CREATE TABLE PLC_Data
(
    ID BIGINT IDENTITY(1,1) PRIMARY KEY,

    CompanyID INT,

    TagName NVARCHAR(100),

    Value FLOAT,

    Timestamp DATETIME DEFAULT GETDATE()
);
GO



-- =====================================
-- Alarm History
-- =====================================

CREATE TABLE AlarmHistory
(
    AlarmID BIGINT IDENTITY(1,1) PRIMARY KEY,

    CompanyID INT,

    AlarmText NVARCHAR(500),

    AlarmValue FLOAT,

    Timestamp DATETIME DEFAULT GETDATE()
);
GO



-- =====================================
-- Flow Storage
-- =====================================

CREATE TABLE Flows
(
    FlowID INT IDENTITY(1,1) PRIMARY KEY,

    CompanyID INT,

    FlowJson NVARCHAR(MAX),

    LastModified DATETIME DEFAULT GETDATE()
);
GO



-- =====================================
-- Insert Master User
-- =====================================

INSERT INTO Users
(
    Username,
    PasswordHash,
    CompanyID,
    Role,
    Enabled
)
VALUES
(
    'master',
    '1234',
    NULL,
    'Master',
    1
);
GO



-- =====================================
-- Create first company
-- =====================================

INSERT INTO Companies
(
    CompanyName
)
VALUES
(
    'Demo Company'
);
GO



-- =====================================
-- Create demo PLC
-- =====================================

INSERT INTO PLCs
(
    CompanyID,
    PLC_Name,
    PLC_IP,
    PLC_Port,
    Slave_ID
)
VALUES
(
    1,
    'Kinco PLC',
    '192.168.1.10',
    502,
    1
);
GO



-- =====================================
-- Insert basic tags
-- =====================================

INSERT INTO Tags
(
    CompanyID,
    TagName,
    RegisterAddress,
    DataType,
    Description
)
VALUES

(1,'Voltage12',135,'INT','Line Voltage 1-2'),

(1,'Voltage13',136,'INT','Line Voltage 1-3'),

(1,'Voltage23',137,'INT','Line Voltage 2-3'),

(1,'Voltage1',138,'INT','Phase Voltage 1'),

(1,'Voltage2',139,'INT','Phase Voltage 2'),

(1,'Voltage3',140,'INT','Phase Voltage 3'),

(1,'Current1',141,'INT','Current Phase 1'),

(1,'Current2',142,'INT','Current Phase 2'),

(1,'Current3',143,'INT','Current Phase 3');

GO