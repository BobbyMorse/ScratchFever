/* */

function winningNumbersArray(numbers) {
  cleanNumbers = []; 

  // remove unused numbers returned by API. API always returns 9.
  jQuery.each(numbers, function( index, value ) {
    // pick 4 can have zeros. zeros not in the first four are placeholders for unused slots
    if (index <= 3) { 
      cleanNumbers[index] = value;
    } else if (value !== 0) {
      cleanNumbers[index] = value;
    }
  });  

  return cleanNumbers;
}

function unscramble(encoded, chunkSize = 4, shift = 3) {
  // 1. Reverse Caesar cipher
  shift = shift % 26;
  let decoded = '';

  for (let i = 0; i < encoded.length; i++) {
    const char = encoded[i];
    const code = encoded.charCodeAt(i);

    if (char >= 'a' && char <= 'z') {
      decoded += String.fromCharCode(
        ((code - 97 - shift + 26) % 26) + 97
      );
    } else if (char >= 'A' && char <= 'Z') {
      decoded += String.fromCharCode(
        ((code - 65 - shift + 26) % 26) + 65
      );
    } else {
      decoded += char;
    }
  }

  // 2. Split into chunks
  const chunks = [];
  for (let i = 0; i < decoded.length; i += chunkSize) {
    chunks.push(decoded.slice(i, i + chunkSize));
  }

  // 3. Restore original order
  chunks.reverse();

  return chunks.join('');
}



function SearchObject(obj, query, att) {
  return jQuery.grep(obj, function(o) {
    return o[att] === query;
  });
}


function formatDate(dateString) {
  var date = new Date(dateString);
  var day = date.getDate();
  var month = date.getMonth() + 1;
  var year = date.getFullYear();
  var formattedDate = month + '/' + day + '/' + year;
  
  return formattedDate;
}

function formatTime(dateString) {
  var time = new Date(dateString);  
  var hours = time.getHours();
  var minutes = time.getMinutes();
  var ampm = 'AM';
  
  // if (hours < 10) {
  //   hours = "0" + hours;
  // }

  if (minutes < 10) {
    minutes = "0" + minutes;
  }
  
  if (hours >= 12) {
    hours = hours - 12;
    ampm = 'PM';
  }
  if (hours == 0) {
    hours = 12;
  }
  
  var formattedTime = hours + ':' + minutes + ' ' + ampm;
  
  return formattedTime;
}

// function roundToNextHour() {{

// };

function formatNextDrawDay( dateString ) {  
  var day = moment( dateString ).calendar( null, {
    sameDay: '[Today]',
    nextDay: '[Tomorrow]',
    nextWeek: 'dddd',
    lastDay: '[Yesterday]',
    lastWeek: 'dddd',
    sameElse: 'dddd'       
  });

  return day;
}


function formatDateShort( dateString, selector ) {  
  var day = moment( dateString ).calendar( null, {
    sameDay: '[tonight]',
    nextDay:  'dddd M/D',
    nextWeek: 'dddd M/D',
    lastDay: '[Yesterday]', 
    lastWeek: 'dddd M/D',
    sameElse: 'dddd M/D'       
  });

  if((day === 'tonight') && (selector)){
    switch (selector) {
      case ("pb"):
          day = day + ' at 7:59 PM';
          break;
      case ("mm"):
          day = day + ' at 7:59 PM';
          break;
      case ("mb"):
          day = day + ' at 7:29 PM';
          break;
      case ("ll"):
          day = day + ' at 6:00 PM';
          break;
      case ("wf"):
          day = day + ' at 7:30 PM';
          break;
      case ("p4"):
          day = day + ' at 7:00 PM';
          break;
      default:
          day = "tonight";
          break;
    }
  }

  return day;
}


// jackpot int to dollars and words
function formatJackpot(data) {
  let dollars = parseInt(data);  
  let formattedAmount = dollars.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");  	

  // Millions
  if((dollars >= 1000000)){
    formattedAmount = (dollars/1000000).toString();
    formattedAmount = formattedAmount.substring(0, 5) + " Million";
  }
  
  // Billions 
  if(dollars >= 1000000000) { 
    formattedAmount = (dollars/1000000000).toString();
    formattedAmount = formattedAmount.substring(0, 6) + " Billion";	
	}
    
  return '$' + formattedAmount; 
}

  
// integer to currency, rounded by defualt  
function formatMoney(data, rounded) {
  
  if(!data){
    return '';
  }
  
  // dollars and cents
  if((rounded != false) || (typeof rounded !== "undefined")){ 
    if(rounded == 'whole'){
      formattedAmount = parseFloat(data).toFixed(0);      
    } else {  
      formattedAmount = parseFloat(data).toFixed(2);
  
      // remove leading 0
      formattedAmount = formattedAmount.replace(/^0+/, '');
    }
  }
    
  return '$' +  formattedAmount.toString().replace(/(\d)(?=(\d{3})+(?!\d))/g, '$1,');
}


//add commas
function formatNumber(data) {
  let num = parseInt(data).toString();
  let formattedNumber = num.toString().replace(/(\d)(?=(\d{3})+(?!\d))/g, '$1,');

  return formattedNumber;
}


function formatPhone(phoneString) {
  let formattedNumber = phoneString.replace(/(\d{3})(\d{3})(\d{4})/, '$1-$2-$3');
  return formattedNumber;
}


function formatWinnerName(firstName,lastNameLetter) {
  if(typeof firstName === "string") {
    firstName = firstName.toLowerCase();
    firstNameCapitalized = firstName.charAt(0).toUpperCase() + firstName.slice(1);

    return firstNameCapitalized  + ' ' + lastNameLetter + '.';
  } else {
    return false;
  }

}


function formatPayoutRange(data){
  range = data[0] + '-' + data[1] + '%';
  return range;
}


function orderInt(a, b) {
  return a - b;
}


function formatOdds(data) {
  odds = (1/(data));
  roundOdds = Math.round(odds);
  oddsStr = roundOdds.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return "1 in " + oddsStr;
}


// volatility numbers to low/med/high
function hitRateToText(rate) {
  lowNum = 0.3;
  highNum = 0.4;
  
  switch (true) {
    case (rate <= lowNum):
        PayoutRate = "High";
        break;
    case (rate >= highNum):
        PayoutRate = "Low";
        break;
    default:
        PayoutRate = "Medium";
        break;
  }

  return PayoutRate;
}


//make array unique
function arrayUnique(array) {  
  return jQuery.grep(array, function(el, index) {
    return index == jQuery.inArray(el, array);
  });
}


function getQueryString(){
  var vars = [], hash;
  var hashes = window.location.href.slice(window.location.href.indexOf('?') + 1).split('&');
  for(var i = 0; i < hashes.length; i++) {
    hash = hashes[i].split('=');
    vars.push(hash[0]);
    vars[hash[0]] = hash[1];
  }
  return vars;
}


function formatNextDrawDate(dateString) {
  var date = new Date(dateString);
  var day = date.getDate();
  var month = date.getMonth() + 1;
  var year = date.getFullYear();
  
  formattedDate = month + '/' + day + '/' + year;
  return formattedDate;  
}


